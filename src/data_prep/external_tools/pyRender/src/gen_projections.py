import numpy as np
import sys
import os
import os.path
import trimesh
import pathlib
from core.util.mesh.plot import plot_mesh
import matplotlib.pyplot as plt
from data_prep.external_tools.pyRender.lib import render

def run(filename, out_dir,error_path, scale):

	pc = trimesh.load(filename,'obj')
	V = np.array(pc.vertices) * scale
	F = np.array(pc.faces)
	bbox_x = [np.min(V[:, 0]), np.max(V[:, 0])]
	bbox_y = [np.min(V[:, 1]), np.max(V[:, 1])]
	bbox_z = [np.min(V[:, 2]), np.max(V[:, 2])]
	center = 0.5 * np.array([bbox_x[0] + bbox_x[1], bbox_y[0] + bbox_y[1], bbox_z[0] + bbox_z[1]])
	V = V - np.expand_dims(center, axis=0)
	V = V.astype(np.float32)
	F = F.astype(np.int32)

	# set up camera information,
	info = {'Height':480, 'Width':640, 'fx':575, 'fy':575, 'cx':319.5, 'cy':239.5}
	render.setup(info)

	# set up mesh buffers in cuda
	context = render.SetMesh(V, F)
	cam2world = np.array([[ 0.85408425,  0.31617427, -0.375678  ,  0.56351697 * 2],
		   [ 0.        , -0.72227067, -0.60786998,  0.91180497 * 2],
		   [-0.52013469,  0.51917219, -0.61688   ,  0.92532003 * 2],
		   [ 0.        ,  0.        ,  0.        ,  1.        ]], dtype=np.float32)


	# TODO: add options for more elevations
	# rotate the mesh elevation by 30 degrees
	Rx = np.array([[ 1, 0, 0, 0],
			   [ 0.        , np.cos(np.pi/6), -np.sin(np.pi/6), 0],
			   [0, np.sin(np.pi/6),  np.cos(np.pi/6), 0],
			   [ 0.        ,  0.        ,  0.        ,  1.        ]], dtype=np.float32)
	cam2world = np.matmul(Rx,cam2world)

	# TODO: add option for different angles
	# rotate along y axis and render
	for i_ang, ang in enumerate(np.linspace(0,2*np.pi, 10)):

		Ry = np.array([[ np.cos(ang),  0, -np.sin(ang)  ,  0],
			   [ 0.        , 1, 0,  0],
			   [np.sin(ang),  0, np.cos(ang)   ,  0],
			   [ 0.        ,  0.        ,  0.        ,  1.        ]], dtype=np.float32)
		world2cam = np.linalg.inv(np.matmul(Ry,cam2world)).astype('float32')


		# the actual rendering process
		render.render(context, world2cam)

		# get depth information
		depth = render.getDepth(info)
		#plt.imshow(depth)

		# get information of mesh rendering
		# vindices represents 3 vertices related to pixels
		# vweights represents barycentric weights of the 3 vertices
		# findices represents the triangle index related to pixels
		vindices, vweights, findices = render.getVMap(context, info)
		mask = np.unique(vindices)
		if len(mask) == 1:
			print("ERROR: WRONG MASK !!!", filename)
			if not os.path.isfile(error_path):
				open(error_path, "x")
			f = open(error_path, "a")
			f.write(filename + "\n")
			f.close()
			break


		out_name = os.path.join(out_dir, f"{str(i_ang)}")
		pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
		#plot_mesh(V[mask,:], strategy='spheres', grid_on=True)

		np.savez(out_name, mask=mask)
		#print(out_name)

	render.Clear()

