import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.patches import Polygon

from helpers import correctMeas, loadData, linInterpolate

def main():
    dists = [1.0, 2.0]
    angles = [0, 22, 45, 67, 90]
    object = 'large'
    surfaces = ['cardboard', 'plexiglas']
    sensors = ['HC-SR04', 'URM37', 'MB1603']

    # Create colormap
    cmap = plt.colormaps.get_cmap('plasma')
    # cNorm  = plt.Normalize(vmin=0, vmax=0.9)
    cNorm = LogNorm(vmin=0.01, vmax=2)

    # Create a figure and axis with polar projection
    fig, axis = plt.subplots(ncols=len(surfaces), nrows=len(sensors), subplot_kw={'projection': 'polar'}, figsize=(10,9))
    # fig.suptitle(sensor, fontsize=16, weight='bold')

    for s, sensor in enumerate(sensors):
        for l, surface in enumerate(surfaces):
            ax = axis[s,l]

            # load dataframe
            df = loadData(sensor=sensor, object=object, surface=surface, measurement="third")

            # get mean, std and ratio for each distance and angle
            means = np.zeros((len(dists), len(angles)), dtype=float)
            stds = np.zeros((len(dists), len(angles)), dtype=float)
            ma_error = np.zeros((len(dists), len(angles)), dtype=float)
            rma_error = np.zeros((len(dists), len(angles)), dtype=float)
            for i, dist in enumerate(dists):

                for j, angle in enumerate(angles):
                    if f"{dist}m_{int(angle)}deg" in df.columns:
                        meas = df[f"{dist}m_{int(angle)}deg"].values
                    elif f"{int(dist)}m_{int(angle)}deg" in df.columns:
                        meas = df[f"{int(dist)}m_{int(angle)}deg"].values
                    
                    meas = correctMeas(meas=meas, first_meas=False)      

                    means[i,j] = np.mean(meas)
                    stds[i,j] = np.std(meas)
                    ma_error[i,j] = np.mean(np.abs(meas - dist))
                    rma_error[i,j] = np.mean(np.abs(meas - dist)) / dist

                    ax.scatter([np.deg2rad(angle)]*len(meas), meas, s=15, color=cmap(cNorm(ma_error[i,j])))

            aa = np.deg2rad(linInterpolate(data=angles, check_for_invalid_data=False))
            for i, dist in enumerate(dists):
                mm = linInterpolate(data=means[i])
                ss = linInterpolate(data=stds[i])

                colours = cmap(cNorm(ma_error[i]))
                colours = np.concatenate((linInterpolate(data=colours[:,0]).reshape(-1,1), 
                                          linInterpolate(data=colours[:,1]).reshape(-1,1), 
                                          linInterpolate(data=colours[:,2]).reshape(-1,1),
                                          linInterpolate(data=colours[:,3]).reshape(-1,1)), axis=1)
                for j in range(len(aa)-1):
                    # skip if measurement is not available
                    if mm[j] == 0 or mm[j+1] == 0:
                        continue

                    ax.plot(aa[j:j+2], mm[j:j+2], '-', color=colours[j])

                    vertices = [(aa[j],mm[j]-ss[j]), 
                                (aa[j],mm[j]+ss[j]), 
                                (aa[j+1],mm[j+1]+ss[j+1]), 
                                (aa[j+1],mm[j+1]-ss[j+1])]
                    ax.add_patch(
                        Polygon(vertices, closed=False, facecolor=colours[j], edgecolor=None, alpha=0.5)
                    )

            ax.set_theta_offset(np.pi / 2)  # Set the zero angle at the top
            ax.set_thetamin(0)
            ax.set_thetamax(90)

            ax.set_xticks(np.deg2rad([0, 45, 90]), labels=None) 
            ax.set_yticks([1.0, 2.0, 3.0], labels=None)
            ax.set_yticklabels(['1m', '2m', '3m'])           
            if s == 0:
                ax.set_xticklabels(['0°', '45°', '90°'])
            else:
                ax.set_xticklabels([])
            ax.tick_params(axis='both', color="grey", labelsize=11, labelcolor="black", pad=0.5)
            

            ax.set_thetagrids(angles=[0, 22, 45, 67, 90], weight='black', alpha=0.5, labels=None)
            ax.set_rgrids(radii=[1.0, 2.0, 3.0], weight='black', alpha=0.5, labels=None)
            ax.set_ylim([0,4])

            if s == 0:
                ax.set_title(surface.capitalize(), weight='bold', y=1.05, fontsize=13)
            if l == 0:
                ax.set_ylabel(sensor, weight='bold', fontsize=13)     

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=cNorm)
    sm.set_array(angles)
    cbar = plt.colorbar(sm, ax=axis.ravel().tolist()) 
    cbar.set_label('Mean Absolute Error [m]')  # Label for the colorbar
    plt.subplots_adjust(hspace=0.07, wspace=-0.35, right=0.73, left=0)
    plt.savefig("plots/object_tilted.pdf")
    plt.savefig("plots/object_tilted.png")
    plt.show()


if __name__ == '__main__':
    main()