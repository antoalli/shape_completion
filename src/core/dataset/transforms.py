import random
import numbers
from itertools import repeat
from collections.abc import Sequence
from util.mesh.ops import vnrmls, moments, padded_part_by_mask, flip_vertex_mask, trunc_to_vertex_mask
import numpy as np
import math


# ----------------------------------------------------------------------------------------------------------------------
#                                                  Transforms- Abstract
# ----------------------------------------------------------------------------------------------------------------------
from util.mesh.plots import plot_mesh_montage


class Transform:
    def __repr__(self):
        return self.__class__.__name__ + '()'


class Compose(object):
    def __init__(self, transforms):
        self.transforms = transforms

    def __call__(self, x):
        for t in self.transforms:
            x = t(x)
        return x

    def append(self, new_transform):
        self.transforms.append(new_transform)

    def insert(self, index, new_transform):
        self.transforms.insert(index, new_transform)

    def __repr__(self):
        string_t = [str(t) for t in self.transforms]
        return '(' + ",".join(string_t) + ')'


# ----------------------------------------------------------------------------------------------------------------------
#                                               Special Transforms
# ----------------------------------------------------------------------------------------------------------------------

class AlignChannels(Transform):
    def __init__(self, keys, n_channels, uni_faces):
        self.keys = keys
        self.has_uni_faces = uni_faces
        self.n_channels = n_channels

    def __call__(self, x):
        for k in self.keys:
            x[k] = align_channels(x[k], x['f'], self.n_channels)

        if self.has_uni_faces:
            del x['f']
        return x


class PartCompiler(Transform):
    def __init__(self, keys):
        self.keys = keys

    def __call__(self, x):
        # Done last, since we might transform the mask
        for (part_key, mask_key, full_key) in self.keys:
            x[part_key] = padded_part_by_mask(x[mask_key], x[full_key])
        return x


# ----------------------------------------------------------------------------------------------------------------------
#                                               Data Normalization Transforms
# ----------------------------------------------------------------------------------------------------------------------

class NormalizeScale(Transform):
    r"""Centers and normalizes node positions to the interval :math:`(-1, 1)`.
    """

    def __init__(self, slicer=slice(0, 3), keys=('gt', 'tp')):
        self._slicer = slicer
        self._keys = keys
        self.center = Center(slicer, keys)

    def __call__(self, x):
        x = self.center(x)

        for k in self._keys:
            x[k] *= (1 / x[k].abs().max()) * 0.999999

        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(channels={self._slicer},keys={self._keys})'


class Center(Transform):
    def __init__(self, slicer=slice(0, 3), keys=('gt', 'tp')):
        self._slicer = slicer
        self._keys = keys

    def __call__(self, x):
        for k in self._keys:
            x[k][:, self._slicer] -= x[k][:, self._slicer].mean(axis=0, keepdims=True)
        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(channels={self._slicer},keys={self._keys})'


class UniformVertexScale(Transform):
    def __init__(self, scale, keys=('gt', 'tp')):
        self._scale = scale
        self._keys = keys

    def __call__(self, x):
        for k in self._keys:
            x[k][:, 0:3] = x[k][:, 0:3] * self._scale
        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(scale={self._scale},keys={self._keys})'


# ----------------------------------------------------------------------------------------------------------------------
#                                               Data Augmentation Transforms
# ----------------------------------------------------------------------------------------------------------------------

class RandomMaskFlip(Transform):
    def __init__(self, prob, keys=('gt',)):  # Probability of mask flip
        self._prob = prob
        self._keys = keys
        self._mask_keys = [k + '_mask' for k in keys]  # TODO - Review for more than one mask

    def __call__(self, x):
        for (k, mk) in zip(self._keys, self._mask_keys):
            if random.random() < self._prob:
                nv = x[k].shape[0]
                x[mk] = flip_vertex_mask(nv, x[mk])
        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(prob={self._prob},keys={self._keys})'


class MaskDecimation(Transform):
    def __init__(self, frac, keys=('gt',)):  # Probability of mask flip
        assert frac <= 1 and frac > 0, "Invalid decimation fraction"
        self._frac = frac
        self._keys = keys
        self._mask_keys = [k + '_mask' for k in keys]  # TODO - Review for more than one mask

    def __call__(self, x):
        for (k, mk) in zip(self._keys, self._mask_keys):
            num_mask_vi = x[mk].shape[0]
            num_to_remove = int(num_mask_vi * self._frac)
            assert num_to_remove < num_mask_vi, "Decimated the entire mask - trying upping decimation frac"
            keepers_i = sorted(np.random.choice(range(num_mask_vi), size=num_mask_vi - num_to_remove, replace=False))
            x[mk] = x[mk][keepers_i]
        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(frac={self._frac},keys={self._keys})'


class RandomScale(Transform):
    """
    scales (tuple): scaling factor interval, e.g. :obj:`(a, b)`, then scale
    is randomly sampled from the range
    """

    def __init__(self, scales, keys=('gt', 'tp')):
        assert isinstance(scales, (tuple, list)) and len(scales) == 2
        self._scales = scales
        self._keys = keys

    def __call__(self, x):
        for k in self._keys:
            scale = np.random.uniform(*self._scales)
            x[k][:, :3] *= scale
        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(scales={self._scales},keys={self._keys})'


class RandomRotate(Transform):
    r"""Rotates node positions around a specific axis by a randomly sampled
    factor within a given interval.

    Args:
        degrees (tuple or float): Rotation interval from which the rotation
            angle is sampled. If :obj:`degrees` is a number instead of a
            tuple, the interval is given by :math:`[-\mathrm{degrees},
            \mathrm{degrees}]`.
        axis (int, optional): The rotation axis. (default: :obj:`0`)
    """

    def __init__(self, degrees, axis=0, keys=('gt', 'tp')):
        if isinstance(degrees, numbers.Number):
            degrees = (-abs(degrees), abs(degrees))
        assert isinstance(degrees, (tuple, list)) and len(degrees) == 2
        assert axis in [0, 1, 2]
        self._degrees = degrees
        self._axis = axis
        self._keys = keys

    def __call__(self, x):
        for k in self._keys:
            degree = math.pi * random.uniform(*self._degrees) / 180.0
            sin, cos = math.sin(degree), math.cos(degree)

            if self._axis == 0:
                rot = np.array([[1, 0, 0], [0, cos, sin], [0, -sin, cos]])
            elif self._axis == 1:
                rot = np.array([[cos, 0, -sin], [0, 1, 0], [sin, 0, cos]])
            else:
                rot = np.array([[cos, sin, 0], [-sin, cos, 0], [0, 0, 1]])
            x[k][:, :3] = np.matmul(x[k][:, :3], rot)
            if x[k].shape[1] >= 6:
                x[k][:, 3:6] = np.matmul(x[k][:, 3:6], rot)  # Rotate normals as well

    def __repr__(self):
        return self.__class__.__name__ + f'(degrees={self._degrees},axis={self._axis},keys={self._keys})'


class RandomTranslate(Transform):
    r"""Translates node positions by randomly sampled translation values
    within a given interval. In contrast to other random transformations,
    translation is applied separately at each position.

    Args:
        translate (sequence or float or int): Maximum translation in each
            dimension, defining the range
            :math:`(-\mathrm{translate}, +\mathrm{translate})` to sample from.
            If :obj:`translate` is a number instead of a sequence, the same
            range is used for each dimension.
    WARNING: After this operation, vertex normals are not going to fit- TODO
    """

    def __init__(self, translate, keys=('gt', 'tp')):
        self._translate = translate
        self._keys = keys

    def __call__(self, x):
        for k in self._keys:
            nv, t = x[k].shape[0], self._translate
            if isinstance(t, numbers.Number):
                t = list(repeat(t, times=3))
            assert len(t) == 3

            ts = []
            for d in range(3):
                ts.append(np.random.uniform(low=-abs(t[d]), high=abs(t[d]), size=(nv,)))
                # ts.append(x[k].empty_like(n).uniform_(-abs(t[d]), abs(t[d])))

            x[k][:, :3] += np.stack(ts, axis=1)
        return x

    def __repr__(self):
        return self.__class__.__name__ + f'(translate={self._translate},keys={self._keys})'

class RandomGaussianNoise(Transform):
    # WARNING: After this operation, vertex normals are not going to fit- TODO
    # STD - Either a range or a float
    # Output keys must be same as keys or completely new
    def __init__(self, std, keys=('gt',),okeys=('gt',), slicer=slice(0, 3)):
        if isinstance(std,Sequence):
            std = tuple(std)
        self._std = std
        self._keys = keys
        self._okeys = okeys
        self._slicer = slicer

    def __call__(self, x):
        for ok,k in zip(self._okeys,self._keys):
            arr = x[k][:,self._slicer]
            std = np.random.uniform(low=self._std[0],high=self._std[1]) if isinstance(self._std,tuple) else self._std
            noise = (std * np.random.standard_normal(arr.shape)).astype(arr.dtype)
            if ok == k:
                x[k][:,self._slicer] = arr + noise
            else:
                x[ok] = arr + noise
        return x

    def __repr__(self):
        return self.__class__.__name__ + \
               f'(std={self._std},keys={self._keys},okeys={self._okeys},slicer={self._slicer})'
# ----------------------------------------------------------------------------------------------------------------------
#                                               Data Augmentation Transforms
# ----------------------------------------------------------------------------------------------------------------------

def align_channels(v, f, req_in_channels):
    available_in_channels = v.shape[1]
    if available_in_channels > req_in_channels:
        return v[:, 0:req_in_channels]
    else:
        combined = [v]
        if req_in_channels >= 6 > available_in_channels:
            combined.append(vnrmls(v, f))
        if req_in_channels >= 12 > available_in_channels:
            combined.append(moments(v))

        return np.concatenate(combined, axis=1)


# ----------------------------------------------------------------------------------------------------------------------
#
# ----------------------------------------------------------------------------------------------------------------------

def test_suite():
    from dataset.datasets import FullPartDatasetMenu
    from util.mesh.plots import plot_mesh
    ds = FullPartDatasetMenu.get('FaustPyProj')
    single_ldr = ds.loaders(s_nums=1000, s_shuffle=True, s_transform=[RandomGaussianNoise((0,0.05),okeys=('gt_noise',))],
                            n_channels=6, method='rand_f2f', batch_size=1, device='cpu-single')
    for dp in single_ldr:
        dp['gt'] = dp['gt'].squeeze()
        gt = dp['gt']
        # mask = dp['gt_mask'][0]
        # gt_part = gt[mask, :]
        # trans = RandomTranslate(0.01, keys=['gt'])
        # print(trans)
        # v = gt_part[:, :3]
        # n = gt_part[:, 3:6]
        # _, f = trunc_to_vertex_mask(gt[:, :3], ds.faces(), mask)
        plot_mesh_montage(vb=[gt[:, :3],dp['gt_noise']],fb=ds.faces(),strategy='spheres')
        dp = trans(dp)
        v = gt[:, :3]
        n = gt[:, 3:6]
        plot_mesh(v=v, n=n, f=ds.faces(), strategy='mesh')
        break


if __name__ == '__main__':
    test_suite()
