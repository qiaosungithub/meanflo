"""ImageNet input pipeline."""

import os
import random
from functools import partial

import jax
import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler
from torchvision import datasets, transforms

from utils.logging_util import log_for_0

IMAGE_SIZE = 224
CROP_PADDING = 32
MEAN_RGB = [0.485, 0.456, 0.406]
STDDEV_RGB = [0.229, 0.224, 0.225]

def center_crop_arr(pil_image, image_size):
    """
    Center cropping implementation from ADM.
    https://github.com/openai/guided-diffusion/blob/8fb3ad9197f16bbc40620447b2742e13458d2831/guided_diffusion/image_datasets.py#L126
    """
    while min(*pil_image.size) >= 2 * image_size:
        pil_image = pil_image.resize(
            tuple(x // 2 for x in pil_image.size), resample=Image.BOX
        )

    scale = image_size / min(*pil_image.size)
    pil_image = pil_image.resize(
        tuple(round(x * scale) for x in pil_image.size), resample=Image.BICUBIC
    )

    arr = np.array(pil_image)
    crop_y = (arr.shape[0] - image_size) // 2
    crop_x = (arr.shape[1] - image_size) // 2
    return Image.fromarray(arr[crop_y: crop_y + image_size, crop_x: crop_x + image_size])


def prepare_batch_data(batch, batch_size=None):
  """
  Reformat a input batch from PyTorch Dataloader.
  
  Args: (torch)
    batch = (image, label)
      image: shape (host_batch_size, 2*C, H, W)
      label: shape (host_batch_size)
      the channel is 2*C because the latent manager returns both mean and std
    batch_size = expected batch_size of this node, for eval's drop_last=False only

  Returns: a dict (numpy)
    image shape (local_devices, device_batch_size, H, W, 2*C)
  """
  image, label = batch

  # pad the batch if smaller than batch_size
  if batch_size is not None and batch_size > image.shape[0]:
    image = torch.cat([image, torch.zeros((batch_size - image.shape[0],) + image.shape[1:], dtype=image.dtype)], axis=0)
    label = torch.cat([label, -torch.ones((batch_size - label.shape[0],), dtype=label.dtype)], axis=0)

  # reshape (host_batch_size, 3, height, width) to
  # (local_devices, device_batch_size, height, width, 3)
  local_device_count = jax.local_device_count()
  image = image.permute(0, 2, 3, 1)
  image = image.reshape((local_device_count, -1) + image.shape[1:])
  label = label.reshape(local_device_count, -1)

  image = image.numpy()
  label = label.numpy()

  return_dict = {
    'image': image,
    'label': label,
  }

  return return_dict


def worker_init_fn(worker_id, rank):
    seed = worker_id + rank * 1000
    torch.manual_seed(seed)
    random.seed(seed)
    np.random.seed(seed)


from torchvision.datasets.folder import pil_loader


def loader(path: str):
    return pil_loader(path)


def create_cifar_split(dataset_cfg, batch_size, split, use_flip):
  """Creates a split from the CIFAR dataset using Torchvision Datasets.

  Args:
    dataset_cfg: Configurations for the dataset.
    batch_size: Batch size for the dataloader.
    split: 'train' or 'val'.
  Returns:
    it: A PyTorch Dataloader.
    steps_per_epoch: Number of steps to loop through the DataLoader.
  """
  # copied from our FM repo
  assert split == 'train'
  name = dataset_cfg.name.upper()
  ds = getattr(datasets,name)(
    root='~/cache',
    train=True,
    download=True,
    transform=transforms.Compose([
      transforms.RandomHorizontalFlip(p=0.5),
      transforms.ToTensor(),
      transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
    ]),
  )
  log_for_0(ds)
  rank = jax.process_index()
  sampler = DistributedSampler(
    ds,
    num_replicas=jax.process_count(),
    rank=rank,
    shuffle=True,
  )
  it = DataLoader(
    ds, batch_size=batch_size, drop_last=True,
    worker_init_fn=partial(worker_init_fn, rank=rank),
    sampler=sampler,
    num_workers=dataset_cfg.num_workers,
    prefetch_factor=dataset_cfg.prefetch_factor if dataset_cfg.num_workers > 0 else None,
    pin_memory=dataset_cfg.pin_memory,
    persistent_workers=True if dataset_cfg.num_workers > 0 else False,
  )
  steps_per_epoch = len(it)
  return it, steps_per_epoch

from utils.vae_util import LatentDataset


def create_latent_split(dataset_cfg, batch_size, split):
    """Creates a split from the Latent of ImageNet dataset using Torchvision Datasets.

    Args:
      dataset_cfg: Configurations for the dataset.
      batch_size: Batch size for the dataloader.
      split: 'train' or 'val'.
    Returns:
      it: A PyTorch Dataloader.
      steps_per_epoch: Number of steps to loop through the DataLoader.
    """
    # copied from our FM repo
    assert split == 'train'
    name = dataset_cfg.name.upper()
    ds = LatentDataset(
      root=os.path.join(dataset_cfg.root, split),
      use_flip=True,
    )
    log_for_0(ds)
    rank = jax.process_index()
    sampler = DistributedSampler(
      ds,
      num_replicas=jax.process_count(),
      rank=rank,
      shuffle=True,
    )
    it = DataLoader(
      ds, batch_size=batch_size, drop_last=True,
      worker_init_fn=partial(worker_init_fn, rank=rank),
      sampler=sampler,
      num_workers=dataset_cfg.num_workers,
      prefetch_factor=dataset_cfg.prefetch_factor if dataset_cfg.num_workers > 0 else None,
      pin_memory=dataset_cfg.pin_memory,
      persistent_workers=True if dataset_cfg.num_workers > 0 else False,
    )
    steps_per_epoch = len(it)
    return it, steps_per_epoch


def create_split(
    dataset_cfg,
    batch_size,
    split,
    use_flip=True
):
  """Creates a split from the ImageNet dataset using Torchvision Datasets.

  Args:
    dataset_cfg: Configurations for the dataset.
    batch_size: Batch size for the dataloader.
    split: 'train' or 'val'.
  Returns:
    it: A PyTorch Dataloader.
    steps_per_epoch: Number of steps to loop through the DataLoader.
  """
  if 'cifar' in dataset_cfg.name.lower():
    return create_cifar_split(dataset_cfg, batch_size, split, use_flip)
  if 'latent' in dataset_cfg.name.lower():
    return create_latent_split(dataset_cfg, batch_size, split)
  rank = jax.process_index()
  if use_flip:
    transform_to_use = transforms.Compose([
        transforms.Lambda(lambda pil_image: center_crop_arr(pil_image, dataset_cfg.image_size)),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5], inplace=True)
      ])
  else:
    transform_to_use = transforms.Compose([
        transforms.Lambda(lambda pil_image: center_crop_arr(pil_image, dataset_cfg.image_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5], inplace=True)
      ])
  if split == 'train':
    ds = datasets.ImageFolder(
      os.path.join(dataset_cfg.root, split),
      transform=transform_to_use,
      loader=loader,
    )
    log_for_0(ds)
    sampler = DistributedSampler(
      ds,
      num_replicas=jax.process_count(),
      rank=rank,
      shuffle=True,
    )
    it = DataLoader(
      ds, batch_size=batch_size, drop_last=True,
      worker_init_fn=partial(worker_init_fn, rank=rank),
      sampler=sampler,
      num_workers=dataset_cfg.num_workers,
      prefetch_factor=dataset_cfg.prefetch_factor if dataset_cfg.num_workers > 0 else None,
      pin_memory=dataset_cfg.pin_memory,
      persistent_workers=True if dataset_cfg.num_workers > 0 else False,
    )
    steps_per_epoch = len(it)
  elif split == 'val':
    ds = datasets.ImageFolder(
      os.path.join(dataset_cfg.root, split),
      transform=transform_to_use,
      loader=loader,
    )
    log_for_0(ds)
    '''
    The val has 50000 images. We want to eval exactly 50000 images. When the
    batch is too big (>16), this number is not divisible by the batch size. We
    set drop_last=False and we will have a tailing batch smaller than the batch
    size, which requires modifying some eval code.
    '''
    sampler = DistributedSampler(
      ds,
      num_replicas=jax.process_count(),
      rank=rank,
      shuffle=False,
    )
    it = DataLoader(
      ds, batch_size=batch_size,
      drop_last=False,
      worker_init_fn=partial(worker_init_fn, rank=rank),
      sampler=sampler,
      num_workers=dataset_cfg.num_workers,
      prefetch_factor=dataset_cfg.prefetch_factor if dataset_cfg.num_workers > 0 else None,
      pin_memory=dataset_cfg.pin_memory,
      persistent_workers=True if dataset_cfg.num_workers > 0 else False,
    )
    steps_per_epoch = len(it)
  else:
    raise NotImplementedError

  return it, steps_per_epoch

