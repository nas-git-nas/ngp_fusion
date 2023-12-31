import numpy as np
import os
import matplotlib.pyplot as plt

from trainer_RH import TrainerRH
from helpers.geometric_fcts import findNearestNeighbour



def plotTrainerRHSlice(trainer:TrainerRH, res:int, heights_w:list, tolerance_w:float, thresholds:list):   
    # Create subplots
    fig, axes = plt.subplots(ncols=2+len(thresholds), nrows=len(heights_w), figsize=(12,6))
    extent_c = np.array([[trainer.train_dataset.scene.w2c_params["cube_min"], trainer.train_dataset.scene.w2c_params["cube_min"]], 
                         [trainer.train_dataset.scene.w2c_params["cube_max"], trainer.train_dataset.scene.w2c_params["cube_max"]]])
    extent_w = trainer.train_dataset.scene.c2w(pos=extent_c)
    extent = [extent_w[0,0], extent_w[1,0], extent_w[0,1], extent_w[1,1]]
    
    for i, h_w in enumerate(heights_w):
        # estimate density of slice
        density = trainer.evaluateSlice(res=res, height_w=h_w, tolerance_w=tolerance_w) 

        # threshold density   
        density_thresholded = []
        for j, thr in enumerate(thresholds):
            density_thresholded.append(density.copy())
            density_thresholded[j][density < thr] = 0.0
            density_thresholded[j][density >= thr] = 1.0

        # density_thresholded = []
        # for j, thr in enumerate(thresholds):
        #     density_thresholded.append(density.copy())
        #     density_thresholded[j] -= thr
        #     density_thresholded[j] = 1 / (1 + np.exp(-density_thresholded[j]))

        # get ground truth
        slice_map = trainer.train_dataset.scene.getSliceMap(height=heights_w[i], res=res, height_tolerance=tolerance_w, height_in_world_coord=True)

        # plot the ground truth
        ax = axes[i,0]
        ax.imshow(slice_map.T, extent=extent, origin='lower', cmap='viridis')
        if i == 0:
            ax.set_title(f'Ground Truth')
        ax.set_ylabel(f'Height {heights_w[i]}m')

        # Plot the density map for the current subplot
        ax = axes[i,1]
        ax.imshow(density.T, extent=extent, origin='lower', cmap='viridis')
        if i == 0:
            ax.set_title(f'Rendered Density')

        for j, sig_thr in enumerate(density_thresholded):
            ax = axes[i, j+2]
            ax.imshow(sig_thr.T, extent=extent, origin='lower', cmap='viridis')
            if i == 0:
                ax.set_title(f'Threshold = {thresholds[j]}')

    # Adjust layout to prevent overlapping labels
    plt.tight_layout()
    plt.show()

def plotTrainerRHScan(trainer:TrainerRH, res:int, res_angular:int, np_test_pts:int, save_path:str, height_tolerance:float=0.1):

    error, depth_w, depth_w_gt, scan_map_gt, rays_o_c, scan_angles = trainer.evaluateDepth(res=res, res_angular=res_angular, np_test_pts=np_test_pts, height_tolerance=height_tolerance)

    # N = rays_o_c.shape[0] // res_angular
    # if N != np_test_pts:
    #     print(f"WARNING: plotter.plotTrainerRHScan N={N} != np_test_pts={np_test_pts}")
    #     np_test_pts = N

    

    # convert scan depth to position in world coordinate system
    rays_o_w = trainer.test_dataset.scene.c2w(pos=rays_o_c, copy=True)
    depth_pos_w = trainer.test_dataset.scene.depth2pos(rays_o=rays_o_w, scan_depth=depth_w, scan_angles=scan_angles) # (N*M, 2)

    # create slice map
    scan_maps = np.zeros((np_test_pts,res,res))
    scan_maps_idxs = trainer.test_dataset.scene.w2idx(pos=depth_pos_w, res=res) # (N*M, 2)
    scan_maps_idxs = scan_maps_idxs.reshape(np_test_pts, res_angular, 2)
    for i in range(np_test_pts):
        scan_maps[i,scan_maps_idxs[i,:,0],scan_maps_idxs[i,:,1]] = 1.0

    # get slice map gt
    slice_map_gt = trainer.test_dataset.scene.getSliceMap(height=np.mean(rays_o_w[:,2]), res=res, height_tolerance=height_tolerance, height_in_world_coord=True)

    # get slice map
    density_map = trainer.evaluateSlice(res=res, height_w=np.mean(rays_o_w[:,2]), tolerance_w=height_tolerance) 
    density_map[density_map < 10] = 0.0
    density_map[density_map >= 10] = 1.0  

    # convert depth to position in world coordinate system
    pos_w = trainer.test_dataset.scene.depth2pos(rays_o_w, depth_w, scan_angles)
    pos_w_gt = trainer.test_dataset.scene.depth2pos(rays_o_w, depth_w_gt, scan_angles)

    #
    depth_w = depth_w.reshape(np_test_pts, res_angular)
    depth_w_gt = depth_w_gt.reshape(np_test_pts, res_angular)
    pos_w = pos_w.reshape(np_test_pts, res_angular, 2)
    pos_w_gt = pos_w_gt.reshape(np_test_pts, res_angular, 2)

    #
    depth_mae = np.nanmean(np.abs(depth_w - depth_w_gt), axis=1)
    depth_mare = np.nanmean(np.abs((depth_w - depth_w_gt)/ depth_w_gt), axis=1)

    nn_dists = np.zeros((np_test_pts, res_angular))
    for i in range(np_test_pts):
        _, dists = findNearestNeighbour(array1=pos_w_gt[i], array2=pos_w[i])
        nn_dists[i] = dists

    # plot
    fig, axes = plt.subplots(ncols=1+np_test_pts, nrows=4, figsize=(9,9))
    extent = trainer.test_dataset.scene.c2w(pos=np.array([[-0.5,-0.5],[0.5,0.5]]), copy=False)
    extent = extent.T.flatten()

    scan_maps_comb = np.zeros((np_test_pts,res,res))
    for i in range(np_test_pts):
        scan_maps_comb[i] = scan_map_gt + 2*scan_maps[i]
        scan_score = np.sum(scan_maps[i] * scan_map_gt) / np.sum(scan_map_gt)
    density_map_comb = slice_map_gt + 2*density_map
    density_score = np.sum(density_map * slice_map_gt) / np.sum(slice_map_gt)

    # reshape
    rays_o_w = rays_o_w.reshape((-1, res_angular, 3))
    depth_pos_w = depth_pos_w.reshape((-1, res_angular, 2))

    ax = axes[0,0]
    ax.imshow(slice_map_gt.T, origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=np.max(density_map_comb))
    ax.set_ylabel(f'GT', weight='bold')
    ax.set_title(f'Density', weight='bold')
    ax.set_xlabel(f'x [m]')

    ax = axes[1,0]
    ax.imshow(2*density_map.T, origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=np.max(density_map_comb))
    ax.set_ylabel(f'NeRF', weight='bold')
    ax.set_xlabel(f'x [m]')

    ax = axes[2,0]
    ax.imshow(density_map_comb.T, origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=np.max(density_map_comb))
    ax.set_ylabel(f'GT + NeRF', weight='bold')
    ax.set_xlabel(f'x [m]')

    fig.delaxes(axes[3,0])
    

    for i in range(np_test_pts):
        ax = axes[0,i+1]
        ax.imshow(scan_map_gt.T, origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=np.max(scan_maps_comb[i]))
        ax.scatter(rays_o_w[i,0,0], rays_o_w[i,0,1], c='w', s=5)
        ax.scatter(rays_o_w[:,0,0], rays_o_w[:,0,1], c='w', s=5, alpha=0.1)
        ax.set_title(f'Scan {i+1}', weight='bold')
        ax.set_xlabel(f'x [m]')
        ax.set_ylabel(f'y [m]')

        ax = axes[1,i+1]
        ax.imshow(2*scan_maps[i].T,origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=np.max(scan_maps_comb[i]))
        # for i in range(0, rays_o_w.shape[0], nb_pts_step):
        #     for j in range(depth_pos_w.shape[1]):
        #         ax.plot([rays_o_w[i,j,0], depth_pos_w[i,j,0]], [rays_o_w[i,j,1], depth_pos_w[i,j,1]], c='w', linewidth=0.1)
        ax.scatter(rays_o_w[i,0,0], rays_o_w[i,0,1], c='w', s=5)
        ax.scatter(rays_o_w[:,0,0], rays_o_w[:,0,1], c='w', s=5, alpha=0.1)
        ax.set_xlabel(f'x [m]')
        ax.set_ylabel(f'y [m]')
        
        ax = axes[2,i+1]
        ax.imshow(scan_maps_comb[i].T, origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=np.max(scan_maps_comb[i]))
        # for i in range(0, rays_o_w.shape[0], nb_pts_step):
        #     for j in range(depth_pos_w.shape[1]):
        #         ax.plot([rays_o_w[i,j,0], depth_pos_w[i,j,0]], [rays_o_w[i,j,1], depth_pos_w[i,j,1]], c='w', linewidth=0.1)
        ax.scatter(rays_o_w[i,0,0], rays_o_w[i,0,1], c='w', s=5)
        ax.scatter(rays_o_w[:,0,0], rays_o_w[:,0,1], c='w', s=5, alpha=0.1)
        ax.set_xlabel(f'x [m]')
        ax.set_ylabel(f'y [m]')

        ax = axes[3,i+1]
        ax.hist(nn_dists[i], bins=50)
        ax.vlines(np.nanmean(nn_dists[i]), ymin=0, ymax=20, colors='r', linestyles='dashed', label=f'avg.={np.nanmean(nn_dists[i]):.2f}')
        if i == 0:
            ax.set_ylabel(f'Nearest Neighbour', weight='bold')
        else:
            ax.set_ylabel(f'# elements')
        ax.set_xlim([0, np.nanmax(nn_dists)])
        ax.set_ylim([0, 25])
        ax.set_xlabel(f'distance [m]')
        ax.legend()
        ax.set_box_aspect(1)
    
    plt.tight_layout()
    plt.savefig(os.path.join(save_path, "scan.pdf"))
    plt.show()  




def test_plotTrainerRHSlice():
    ckpt_path = "results/rh_anto_livingroom1_depth_loss_10000/model.pth"
    trainer = TrainerRH()
    trainer.loadCheckpoint(ckpt_path=ckpt_path)

    # create slice
    res = 128
    heights_w = [0.7045, 1.045, 1.345] # in scene coordinates (meters)
    tolerance_w = 0.1 # in meters
    thresholds = [5, 10, 15, 20]

    plotTrainerRHSlice(trainer=trainer, res=res, heights_w=heights_w, tolerance_w=tolerance_w, thresholds=thresholds)


def test_plotTrainerRHScan():
    save_path = "results/RH2/trained_models/20231012_1546"
    trainer = TrainerRH(hparams_file=os.path.join("..", save_path, "hparams.json"))
    trainer.loadCheckpoint(ckpt_path=os.path.join(save_path, "model.pth"))

    # create slice
    res = 128
    res_angular = 256
    np_test_pts = 3
    tolerance_w = 0.005 # in meters
    plotTrainerRHScan(
        trainer = trainer, 
        res = res, 
        res_angular = res_angular, 
        np_test_pts = np_test_pts, 
        save_path = save_path, 
        height_tolerance = tolerance_w
    )


if __name__ == '__main__':
    # test_plotTrainerRHSlice()
    test_plotTrainerRHScan()