import numpy as np




from rosbag_wrapper import RosbagWrapper


def main():
    
    data_dir = "/home/spadmin/catkin_ws_ngp/data/office_2"
    bag_name = "office_2_2_sync.bag"
    
    bag_wrapper = RosbagWrapper(
        data_dir=data_dir,
        bag_name=bag_name,
    )   
    
    bag_wrapper.read(
        save_meas=[
            "/CAM1/color/image_raw",
            "/CAM3/color/image_raw",
            "/CAM1/aligned_depth_to_color/image_raw",
            "/CAM3/aligned_depth_to_color/image_raw",
            "/USS1",
            "/USS3",
            "/TOF1",
            "/TOF3",
        ]
    )
    


if __name__ == "__main__":
    main()