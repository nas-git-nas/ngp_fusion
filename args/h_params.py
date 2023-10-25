


class HParams():
    def __init__(self, name):
        self.self_name = name

    def setHParams(self, hparams):
        for key in self.__dict__.keys():
            if key != "self_name":
                setattr(self, key, hparams[self.self_name][key])

    def getHParams(self):
        self_dict = {}
        for key, value in self.__dict__.items():
            if key != "self_name":
                self_dict[key] = value
        return self_dict


class HParamsDataset(HParams):
    def __init__(self) -> None:
        # hyper parameters
        self.path = None
        self.name = None
        self.split_ratio = None
        self.downsample = None
        self.keep_N_observations = None
        self.keep_sensor = None
        self.keep_pixels_in_angle_range = None

        HParams.__init__(self, name="dataset")


class HParamsModel(HParams):
    def __init__(self) -> None:
        # hyper parameters
        self.ckpt_path = None
        self.scale = None
        self.encoder_type = None

        HParams.__init__(self, name="model")
     

class HParamsTraining(HParams):
    def __init__(self) -> None:
        # hyper parameters
        self.distortion_loss_w = None
        self.batch_size = None
        self.sampling_strategy = None
        self.sensors = None
        self.max_steps = None
        self.lr = None
        self.depth_loss_w = None
        self.random_bg = None

        HParams.__init__(self, name="training")
    
    def checkArgs(self):
        if self.sampling_strategy["imgs"] == "all" and self.sampling_strategy["rays"] != "random":
            self.sampling_strategy["rays"] = "random"
            print(f"WARNING: HParamsTraining:checkArgs: sampling strategy for rays must be 'random' if sampling strategy for images is 'all' ")


class HParamsEvaluation(HParams):
    def __init__(self) -> None:
        # hyper parameters
        self.batch_size = None
        self.res_map = None
        self.res_angular = None
        self.eval_every_n_steps = None
        self.num_color_pts = None
        self.num_depth_pts = None
        self.num_plot_pts = None
        self.num_avg_heights = None
        self.height_tolerance = None
        self.density_map_thr = None

        HParams.__init__(self, name="evaluation")


class HParamsOccGrid(HParams):
    def __init__(self) -> None:
        # hyper parameters
        self.warmup_steps = None
        self.update_interval = None
        self.max_res = None

        HParams.__init__(self, name="occ_grid")


class HParamsRobotAtHome(HParams):
    def __init__(self) -> None:
        # hyper parameters
        self.session = None
        self.home = None
        self.room = None
        self.subsession = None
        self.home_session = None

        HParams.__init__(self, name="robot_at_home")


class HParamsRGBD(HParams):
    def __init__(self):
        # hyper parameters
        self.angle_of_view = None

        HParams.__init__(self, name="RGBD")


class HParamsUSS(HParams):
    def __init__(self):
        # hyper parameters
        self.angle_of_view = None

        HParams.__init__(self, name="USS")


class HParamsToF(HParams):
    def __init__(self):
        # hyper parameters
        self.angle_of_view = None
        self.matrix = None

        HParams.__init__(self, name="ToF")