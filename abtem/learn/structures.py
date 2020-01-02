import numpy as np
from matplotlib.path import Path
from scipy.signal import resample
from scipy.spatial import ConvexHull
from sklearn.neighbors import NearestNeighbors

from abtem.learn.augment import bandpass_noise
from abtem.points import LabelledPoints


def graphene_like(a=2.46, n=1, m=1, labels=None):
    basis = [(0, 0), (2 / 3., 1 / 3.)]
    cell = [[a, 0], [-a / 2., a * 3 ** 0.5 / 2.]]
    positions = np.dot(np.array(basis), np.array(cell))

    points = LabelledPoints(positions, cell=cell, labels=labels)
    points.repeat(n, m)
    return points


def random_swap_labels(points, label, new_label, probability):
    idx = np.where(points.labels == label)[0]
    points.labels[idx[np.random.rand(len(idx)) < probability]] = new_label
    return points


def random_strain(points, scale, amplitude):
    shape = np.array((128, 128))
    sampling = np.linalg.norm(points.cell, axis=1) / shape
    outer = 1 / scale * 2
    noise = bandpass_noise(inner=0, outer=outer, shape=shape, sampling=sampling)
    indices = np.floor((points.scaled_positions % 1.) * shape).astype(np.int)

    for i in [0, 1]:
        points.positions[:, i] += amplitude * noise[indices[:, 0], indices[:, 1]]

    return points


def make_blob():
    points = np.random.rand(10, 2)
    points = points[ConvexHull(points).vertices]
    x = resample(points[:, 0], 50)
    y = resample(points[:, 1], 50)
    blob = np.array([x, y]).T
    blob = (blob - blob.min(axis=0)) / blob.ptp(axis=0)
    return blob


def select_blob(points, blob):
    path = Path(blob)
    return path.contains_points(points.positions)


def random_select_blob(points, size):
    blob = size * (make_blob() - .5)
    position = np.random.rand() * points.cell[0] + np.random.rand() * points.cell[1]
    return select_blob(points, position + blob)


def select_close(points, mask, distance):
    nbrs = NearestNeighbors(n_neighbors=1, algorithm='ball_tree').fit(points.positions[mask])

    distances, indices = nbrs.kneighbors(points.positions)
    distances = distances.ravel()

    return (distances < distance) * (mask == 0)


def add_contamination(points, label, position, size, mean_spacing):
    blob = position + size * (make_blob() - .5)

    path = Path(blob)
    x = np.arange(blob[:, 0].min(), blob[:, 0].max(), mean_spacing)
    y = np.arange(blob[:, 1].min(), blob[:, 1].max(), mean_spacing)
    x, y = np.meshgrid(x, y)
    positions = np.array([x.ravel(), y.ravel()]).T
    positions += mean_spacing / 2 * np.random.randn(len(positions), 2)

    contamination = LabelledPoints(positions, labels=np.full(len(positions), 0, dtype=np.int))
    contamination = contamination[path.contains_points(contamination.positions)]
    contamination.labels[:] = label
    points.extend(contamination)
    return points.copy()


def random_add_contamination(points, new_label, size, density):
    position = np.random.rand() * points.cell[0] + np.random.rand() * points.cell[1]
    return add_contamination(points, new_label, position, size, density)
