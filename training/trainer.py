import glob
import os
import time
import tqdm

import warnings

import torch
import imageio
import numpy as np
import pandas as pd
import taichi as ti
from einops import rearrange
import torch.nn.functional as F
from abc import abstractmethod

from alive_progress import alive_bar
from contextlib import nullcontext
import matplotlib.pyplot as plt

# from gui import NGPGUI
# from opt import get_opts
from args.args import Args
from datasets.ray_utils import get_rays
from datasets.dataset_base import DatasetBase

from modules.networks import NGP
from modules.distortion import distortion_loss
from modules.rendering import MAX_SAMPLES, render
from modules.utils import depth2img, save_deployment_model

from modules.networks import NGP
from modules.distortion import distortion_loss
from modules.rendering import MAX_SAMPLES, render
from modules.utils import depth2img, save_deployment_model
from helpers.geometric_fcts import findNearestNeighbour, createScanRays
from helpers.data_fcts import linInterpolateArray, convolveIgnorNans, dataConverged
from training.metrics_rh import MetricsRH

from modules.occupancy_grid import OccupancyGrid

from training.trainer_plot import TrainerPlot
from training.loss import Loss


from torchmetrics import (
    PeakSignalNoiseRatio, StructuralSimilarityIndexMeasure
)

warnings.filterwarnings("ignore")


class Trainer(TrainerPlot):
    def __init__(
        self, 
        hparams_file=None,
        args:Args=None,
        train_dataset:DatasetBase=None,
        test_dataset:DatasetBase=None,
    ) -> None:
        print(f"\n----- START INITIALIZING -----")

        TrainerPlot.__init__(
            self,
            args=args,
            hparams_file=hparams_file,
            train_dataset=train_dataset,
            test_dataset=test_dataset,
        )
        

        # # TODO: remove this
        # self.model.mark_invisible_cells(
        #     self.train_dataset.K,
        #     self.train_dataset.poses, 
        #     self.train_dataset.img_wh,
        # )

        # # use large scaler, the default scaler is 2**16 
        # # TODO: investigate why the gradient is small
        # if self.hparams.half_opt:
        #     scaler = 2**16
        # else:
        #     scaler = 2**19
        scaler = 2**19
        self.grad_scaler = torch.cuda.amp.GradScaler(scaler)

        # optimizer
        self.optimizer = torch.optim.Adam(
            self.model.parameters(), 
            self.args.training.lr, 
            eps=1e-15,
        )

        # scheduler
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer=self.optimizer,
            T_max=self.args.training.max_steps,
            eta_min=self.args.training.lr/30,
        )

        # loss function
        self.loss = Loss(
            args=self.args,
            scene=self.train_dataset.scene,
            sensors_dict=self.train_dataset.sensors_dict,
        )

        # metrics
        self.metrics = MetricsRH(
            args=self.args,
            scene=self.train_dataset.scene,
            img_wh=self.train_dataset.img_wh,
        )

        # initialize logs
        self.logs = {
            'time': [],
            'step': [],
            'loss': [],
            'color_loss': [],
            'depth_loss': [],
            'rgbd_loss': [],
            'ToF_loss': [],
            'USS_loss': [],
            'USS_close_loss': [],
            'USS_min_loss': [],
            'psnr': [],
            'mnn': [],
        }

    def train(self):
        """
        Training loop.
        """
        print(f"\n----- START TRAINING -----")
        train_tic = time.time()
        for step in range(self.args.training.max_steps):
            self.model.train()

            data = self.train_dataset(
                batch_size=self.args.training.batch_size,
                sampling_strategy=self.args.training.sampling_strategy,
                origin="nerf",
            )
            
            with torch.autocast(device_type='cuda', dtype=torch.float16):
                
                if step % self.args.occ_grid.update_interval == 0:

                    if self.args.occ_grid.grid_type == 'nerf':
                        self.model.updateNeRFGrid(
                            density_threshold=0.01 * MAX_SAMPLES / 3**0.5,
                            warmup=step < self.args.occ_grid.warmup_steps,
                        )
                    elif self.args.occ_grid.grid_type == 'occ':
                        self.model.updateOccGrid(
                            density_threshold= 0.5,
                        )
                    else:
                        self.args.logger.error(f"grid_type {self.args.occ_grid.grid_type} not implemented")
                    

                # render image
                results = render(
                    self.model, 
                    data['rays_o'], 
                    data['rays_d'],
                    exp_step_factor=self.args.exp_step_factor,
                )

                # calculate loss
                loss, loss_dict = self.loss(
                    results=results,
                    data=data,
                    return_loss_dict=True, # TODO: optimize
                )
                # loss, color_loss, depth_loss = self.lossFunc(results=results, data=data, step=step)
                if self.args.training.distortion_loss_w > 0:
                    loss += self.args.training.distortion_loss_w * distortion_loss(results).mean()

            # backpropagate and update weights
            self.optimizer.zero_grad()
            self.grad_scaler.scale(loss).backward()
            self.grad_scaler.step(self.optimizer)
            self.grad_scaler.update()
            self.scheduler.step()

            # evaluation
            eval_tic = time.time()
            self._evaluateStep(
                results=results, 
                data=data, 
                step=step, 
                loss_dict=loss_dict,
                tic=train_tic
            )
            self._plotOccGrid(
                step=step,
            )
            eval_toc = time.time()
            train_tic += eval_toc - eval_tic # subtract evaluation time from training time

        self._saveModel()

    def evaluate(self):
        """
        Evaluate NeRF on test set.
        Returns:
            metrics_dict: dict of metrics; dict
        """
        print(f"\n----- START EVALUATING -----")
        self.model.eval()

        # get indices of all test points and of one particular sensor
        img_idxs = np.arange(len(self.test_dataset))
        img_idxs_sensor = self.test_dataset.getIdxFromSensorName( # TODO: add parameter or evaluation positions
            sensor_name="RGBD_1" if self.args.dataset.name == "RH2" else "CAM1",
        )

        # keep only a certain number of points
        if self.args.eval.num_color_pts != "all":
            idxs_temp = np.random.randint(0, len(img_idxs), self.args.eval.num_color_pts)
            # idxs_temp = np.linspace(0, len(img_idxs)-1, self.args.eval.num_color_pts, dtype=int)
            img_idxs = img_idxs[idxs_temp]

        if self.args.eval.num_depth_pts != "all":
            idxs_temp = np.linspace(0, len(img_idxs_sensor)-1, self.args.eval.num_depth_pts, dtype=int)
            img_idxs_sensor = img_idxs_sensor[idxs_temp]

        # evaluate color and depth
        metrics_dict = self._evaluateColor(img_idxs=img_idxs)
        depth_metrics, data_w = self._evaluateDepth(
            img_idxs=img_idxs_sensor,
            return_only_nerf=False,
        )
        for key in depth_metrics.keys():
            depth_metrics[key].update(metrics_dict)
        metrics_dict = depth_metrics

        # create plots
        self._plotEvaluation(
            data_w=data_w, 
            metrics_dict=metrics_dict,
            num_imgs=img_idxs_sensor.shape[0],
        )
        # metrics_dict = self._plotLosses(
        #     logs=self.logs,
        #     metrics_dict=metrics_dict,
        # )

        # print and save metrics
        print(
            f"evaluation: " \
            + f"psnr_avg={np.round(metrics_dict['NeRF']['psnr'],2)} | " \
            + f"ssim_avg={metrics_dict['NeRF']['ssim']:.3} | " \
            + f"depth_mae={metrics_dict['NeRF']['mae']:.3} | " \
            + f"depth_mare={metrics_dict['NeRF']['mare']:.4} | " \
            + f"depth_mnn={metrics_dict['NeRF']['mnn']:.3} | " \
        )


        metric_df = {key:[] for key in metrics_dict["NeRF"].keys()}
        del metric_df['nn_dists']
        metric_idxs = []
        for key in metrics_dict.keys():
            for metric, value in metrics_dict[key].items():
                if metric == "nn_dists":
                    continue
                metric_df[metric].append(value)
            metric_idxs.append(key)

        pd.DataFrame(
            data=metric_df,
            index=metric_idxs,
        ).to_csv(os.path.join(self.args.save_dir, "metrics.csv"), index=True)

        # metrics_df = metrics_dict.copy()
        # del metrics_df['nn_dists_nerf']
        # del metrics_df['nn_dists_tof']
        # del metrics_df['nn_dists_uss']
        # metrics_df = pd.DataFrame(metrics_df, index=[0])
        # metrics_df.to_csv(os.path.join(self.args.save_dir, "metrics.csv"), index=False)

        return metrics_dict

    @torch.no_grad()
    def _evaluateStep(
            self, 
            results:dict, 
            data:dict, 
            step:int, 
            loss_dict:dict,
            tic:time.time,
    ):
        """
        Print statistics about the current training step.
        Args:
            results: dict of rendered images
                'opacity': sum(transmittance*alpha); array of shape: (N,)
                'depth': sum(transmittance*alpha*t__i); array of shape: (N,)
                'rgb': sum(transmittance*alpha*rgb_i); array of shape: (N, 3)
                'total_samples': total samples for all rays; int
                where   transmittance = exp( -sum(sigma_i * delta_i) )
                        alpha = 1 - exp(-sigma_i * delta_i)
                        delta_i = t_i+1 - t_i
            data: dict of ground truth images
                'img_idxs': image indices; array of shape (N,) or (1,) if same image
                'pix_idxs': pixel indices; array of shape (N,)
                'pose': poses; array of shape (N, 3, 4)
                'direction': directions; array of shape (N, 3)
                'rgb': pixel colours; array of shape (N, 3)
                'depth': pixel depths; array of shape (N,)
            step: current training step; int
            loss_dict: dict of sub-losses
            tic: training starting time; time.time()
        """
        # log parameters
        self.logs['time'].append(time.time()-tic)
        self.logs['step'].append(step+1)
        self.logs['loss'].append(loss_dict['total'])
        self.logs['color_loss'].append(loss_dict['color'])
        self.logs['depth_loss'].append(loss_dict['depth'])
        if "rgbd" in loss_dict:
            self.logs['rgbd_loss'].append(loss_dict['rgbd'])
        if "ToF" in loss_dict:
            self.logs['ToF_loss'].append(loss_dict['ToF'])
        if "USS" in loss_dict:
            self.logs['USS_loss'].append(loss_dict['USS'])
            self.logs['USS_close_loss'].append(loss_dict['USS_close'])
            self.logs['USS_min_loss'].append(loss_dict['USS_min'])
        self.logs['psnr'].append(np.nan)
        self.logs['mnn'].append(np.nan)

        # make intermediate evaluation
        if step % self.args.eval.eval_every_n_steps == 0:
            # evaluate color and depth of one random image
            img_idxs = np.array(np.random.randint(0, len(self.test_dataset), size=8))
            depth_metrics, data_w = self._evaluateDepth(
                img_idxs=img_idxs,
                return_only_nerf=True,
            )

            # calculate peak-signal-to-noise ratio
            mse = F.mse_loss(results['rgb'], data['rgb'])
            psnr = -10.0 * torch.log(mse) / np.log(10.0)

            self.logs['psnr'][-1] = psnr.item()
            self.logs['mnn'][-1] = depth_metrics['mnn']
            print(
                f"time={(time.time()-tic):.2f}s | "
                f"step={step} | "
                f"lr={(self.optimizer.param_groups[0]['lr']):.5f} | "
                f"loss={loss_dict['total']:.4f} | "
                f"color_loss={loss_dict['color']:.4f} | "
                f"depth_loss={loss_dict['depth']:.4f} | "
                f"psnr={psnr:.2f} | "
                f"depth_mnn={(depth_metrics['mnn']):.3f} | "
            )  

    @torch.no_grad()
    def _evaluateColor(
            self,
            img_idxs:np.array,
    ):
        """
        Evaluate color error.
        Args:
            img_idxs: image indices; array of int (N,)
        Returns:
            metrics_dict: dict of metrics
        """
        W, H = self.test_dataset.img_wh
        N = img_idxs.shape[0]

        if N == 0:
            return {
                'psnr': -1.0,
                'ssim': -1.0,
            }

        # repeat image indices and pixel indices
        img_idxs = img_idxs.repeat(W*H) # (N*W*H,)
        pix_idxs = np.tile(np.arange(W*H), N) # (N*W*H,)

        # # get poses, direction and color ground truth
        # poses = self.test_dataset.poses[img_idxs]
        # directions_dict = self.test_dataset.directions_dict[pix_idxs]
        # rgb_gt = self.test_dataset.rgbs[img_idxs, pix_idxs][:, :3]

        # # calculate rays
        # rays_o, rays_d = get_rays(
        #     directions=directions, 
        #     c2w=poses
        # ) # (N*W*H, 3), (N*W*H, 3)

        data = self.test_dataset(
            img_idxs=torch.tensor(img_idxs, device=self.args.device),
            pix_idxs=torch.tensor(pix_idxs, device=self.args.device),
        )
        rays_o = data['rays_o']
        rays_d = data['rays_d']
        rgb_gt = data['rgb']

        # render rays to get color
        rgb = torch.empty(0, 3).to(self.args.device)
        depth = torch.empty(0).to(self.args.device)
        for results in self._batchifyRender(
                rays_o=rays_o,
                rays_d=rays_d,
                test_time=True,
                batch_size=self.args.eval.batch_size,
            ):
            rgb = torch.cat((rgb, results['rgb']), dim=0)
            depth = torch.cat((depth, results['depth']), dim=0)

        # calculate metrics
        metrics_dict = self.metrics.evaluate(
            data={ 'rgb': rgb, 'rgb_gt': rgb_gt },
            eval_metrics=['psnr', 'ssim'],
            convert_to_world_coords=False,
            copy=True,
        )

        # save example image
        test_idx = 0 # TODO: customize
        print(f"Saving test image {test_idx} to disk")
        
        rgb_path = os.path.join(self.args.save_dir, f'rgb_{test_idx:03d}.png')
        rgb_img = rearrange(rgb[:H*W].cpu().numpy(),'(h w) c -> h w c', h=H) # TODO: optimize
        rgb_img = (rgb_img * 255).astype(np.uint8)
        imageio.imsave(rgb_path, rgb_img)

        depth_path = os.path.join(self.args.save_dir, f'depth_{test_idx:03d}.png')
        depth_img = rearrange(depth[:H*W].cpu().numpy(), '(h w) -> h w', h=H) # TODO: optimize
        depth_img = depth2img(depth_img)
        imageio.imsave(depth_path, depth_img)

        return metrics_dict
    
    @torch.no_grad()
    def _evaluateDepth(
            self, 
            img_idxs:np.array,
            return_only_nerf:bool=False,
    ):
        """
        Evaluate depth error.
        Args:
            img_idxs: image indices; array of int (N,)
            return_only_nerf: return only metrics for NeRF; bool
        Returns:
            metrics_dict: dict of metrics
            data_w: dict of data in world coordinates
        """
        metrics_dict_nerf, data_w_nerf = self._evaluateDepthNeRF(
            img_idxs=img_idxs,
        )
        if return_only_nerf:
            return metrics_dict_nerf, data_w_nerf

        metrics_dict_tof, data_w_tof = self._evaluateDepthSensor(
            img_idxs=img_idxs,
            sensor_name="ToF",
        )

        metrics_dict_uss, data_w_uss = self._evaluateDepthSensor(
            img_idxs=img_idxs,
            sensor_name="USS",
        )

        metrics_dict = {
            "NeRF": metrics_dict_nerf,
            "ToF": metrics_dict_tof,
            "USS": metrics_dict_uss,
        }

        data_w = { 
            'depth_gt': data_w_nerf['depth_gt'],
            'rays_o_nerf': data_w_nerf['rays_o'],
            'rays_o_tof': data_w_tof['rays_o'],
            'rays_o_uss': data_w_uss['rays_o'],
            'scan_map_gt': data_w_nerf['scan_map_gt'],
            'depth_nerf': data_w_nerf['depth'],
            'depth_tof': data_w_tof['depth'],
            'depth_uss': data_w_uss['depth'],
            'scan_angles_nerf': data_w_nerf['scan_angles'],
            'scan_angles_tof': data_w_tof['scan_angles'],
            'scan_angles_uss': data_w_uss['scan_angles'],
        }
        return metrics_dict, data_w

    @torch.no_grad()
    def _evaluateDepthNeRF(
            self, 
            img_idxs:np.array,
    ) -> dict:
        """
        Evaluate depth error.
        Args:
            img_idxs: image indices; array of int (N,)
        Returns:
            metrics_dict: dict of metrics
            data_w: dict of data in world coordinates
        """
        # get positions of image indices
        rays_o_img_idxs = self.test_dataset.poses[img_idxs, :3, 3].detach().clone() # (N, 3)
        sensor_ids = self.test_dataset.sensor_ids[img_idxs].detach().clone() # (N,)

        print(f"before: {rays_o_img_idxs[:5]}")

        # convert positions from camera to lidar coordinate frame
        rays_o_img_idxs = self.test_dataset.camera2lidarPosition(
            xyz=rays_o_img_idxs.cpu().numpy(),
            sensor_ids=sensor_ids.cpu().numpy(),
            pose_given_in_world_coord=False,
        )
        rays_o_img_idxs = torch.tensor(rays_o_img_idxs, device=self.args.device, dtype=torch.float32) # (N, 3)

        print(f"before: {rays_o_img_idxs[:5]}")

        # convert height tolerance to cube coordinates
        h_tol_c = self.test_dataset.scene.w2c(pos=self.args.eval.height_tolerance, only_scale=True, copy=True)

        # create scan rays for averaging over different heights
        rays_o, rays_d = createScanRays(
            rays_o=rays_o_img_idxs,
            res_angular=self.args.eval.res_angular,
            h_tol_c=h_tol_c,
            num_avg_heights=self.args.eval.num_avg_heights,
        ) # (N*M*A, 3), (N*M*A, 3)

        # render rays to get depth
        depths = torch.empty(0).to(self.args.device)
        for results in self._batchifyRender(
                rays_o=rays_o,
                rays_d=rays_d,
                test_time=True,
                batch_size=self.args.eval.batch_size,
            ):
            depths = torch.cat((depths, results['depth']), dim=0)

        # average dpeth over different heights
        depths = depths.detach().cpu().numpy().reshape(-1, self.args.eval.num_avg_heights) # (N*M, A)
        depth = np.nanmean(depths, axis=1) # (N*M,)


        # create scan rays for averaging over different heights
        rays_o, rays_d = createScanRays(
            rays_o=rays_o_img_idxs,
            res_angular=self.args.eval.res_angular,
            h_tol_c=0.0,
            num_avg_heights=1,
        ) # (N*M*A, 3), (N*M*A, 3)

        metrics_dict, data_w = self._evaluateDepthMetric(
            rays_o=rays_o.detach().cpu().numpy(), # (N*M, 3), 
            rays_d=rays_d.detach().cpu().numpy(), # (N*M, 3), 
            depth=depth, 
            num_test_pts=len(img_idxs),
        )
        return metrics_dict, data_w
    
    @torch.no_grad()
    def _evaluateDepthSensor(
            self, 
            img_idxs:np.array,
            sensor_name:str,
    ) -> dict:
        """
        Evaluate depth error.
        Args:
            img_idxs: image indices; array of int (N,)
            sensor_name: name of sensor; str
        Returns:
            metrics_dict: dict of metrics
            data_w: dict of data in world coordinates
        """
        img_idxs = torch.tensor(img_idxs, dtype=torch.int32, device=self.args.device) # (N,)
        W, H = self.test_dataset.img_wh

        # add synchrone samples from other sensor stack
        sync_idxs = self.test_dataset.getSyncIdxs(
            img_idxs=img_idxs,
        )
        img_idxs = sync_idxs.flatten() # N->2*N

        # determine scan pixel height
        sensor_mask = self.test_dataset.sensors_dict[sensor_name].mask.detach().clone().reshape(H,W) # (H, W)
        if sensor_name == "USS":
            scan_pix_h = H//2
        elif sensor_name == "ToF":
            sensor_mask_height = (torch.sum(sensor_mask, dim=1) > 0)
            scan_pix_h = torch.arange(H, device=self.args.device)[sensor_mask_height][3]
        else:
            self.args.logger.error(f"sensor_name {sensor_name} not implemented")

        # get pixel indices of sensor
        scan_mask = torch.zeros_like(sensor_mask, dtype=torch.bool, device=self.args.device) # (H, W)
        scan_mask[scan_pix_h, :] = sensor_mask[scan_pix_h, :] # (H, W)
        scan_mask = scan_mask.reshape(-1) # (H*W,)
        pix_idxs = torch.arange(H*W, dtype=torch.int32, device=self.args.device) # (H*W,)
        pix_idxs = pix_idxs[scan_mask]

        # get positions, directions and depths of sensor
        img_idxs, pix_idxs = torch.meshgrid(img_idxs, pix_idxs, indexing="ij") # (2*N,M), (2*N,M)
        data = self.test_dataset(
            img_idxs=img_idxs.flatten(),
            pix_idxs=pix_idxs.flatten(),
        )

        metrics_dict, data_w = self._evaluateDepthMetric(
            rays_o=data['rays_o'].detach().cpu().numpy(), # (N*2*M, 3),
            rays_d=data['rays_d'].detach().cpu().numpy(), # (N*2*M, 3),
            depth=data['depth'][sensor_name].detach().cpu().numpy(), # (N*M,),
            num_test_pts=len(img_idxs),
        )
        return metrics_dict, data_w

    @torch.no_grad()
    def _evaluateDepthMetric(
            self, 
            rays_o:np.array,
            rays_d:np.array,
            depth:np.array,
            num_test_pts:int,
    ) -> dict:
        """
        Evaluate depth error.
        Args:
            rays_o: ray origins; array of shape (N*M, 3)
            rays_d: ray directions; array of shape (N*M, 3)
            depth: depth; array of shape (N*M,)
            num_test_pts: number of test points N; int
        Returns:
            metrics_dict: dict of metrics
            data_w: dict of data in world coordinates
        """

        # get ground truth depth
        scan_map_gt, depth_gt, scan_angles = self.test_dataset.scene.getSliceScan(
            res=self.args.eval.res_map, 
            rays_o=rays_o, 
            rays_d=rays_d, 
            rays_o_in_world_coord=False, 
            height_tolerance=self.args.eval.height_tolerance
        )

        # convert depth to world coordinates (meters)
        depth_w = self.test_dataset.scene.c2w(pos=depth, only_scale=True, copy=True)
        depth_w_gt = self.test_dataset.scene.c2w(pos=depth_gt, only_scale=True, copy=True)
        rays_o_w = self.test_dataset.scene.c2w(pos=rays_o, copy=True) # (N*M, 3)
        data_w = {
            'depth': depth_w,
            'depth_gt': depth_w_gt,
            'rays_o': rays_o_w,
            'scan_angles': scan_angles,
            'scan_map_gt': scan_map_gt,
        }

        # calculate mean squared depth error
        metrics_dict = self.metrics.evaluate(
            data=data_w,
            eval_metrics=['rmse', 'mae', 'mare', 'nn', 'nn_inv'],
            convert_to_world_coords=False,
            copy=True,
            num_test_pts=num_test_pts,
        )
        return metrics_dict, data_w

    
    

    
    




    


