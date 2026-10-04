"""Explicit, local PyTorch integration for scalar online linear IDBD."""

import torch

from .optimizer import GuardedIDBD, IDBD

__all__ = ['IDBD', 'GuardedIDBD', 'register_torch_optimizer']


def register_torch_optimizer():
    """Opt in to torch.optim aliases; refuse conflicts before changing anything."""
    classes = {'IDBD': IDBD, 'GuardedIDBD': GuardedIDBD}
    for name, cls in classes.items():
        if hasattr(torch.optim, name) and getattr(torch.optim, name) is not cls:
            raise RuntimeError(f'torch.optim.{name} already exists and conflicts')
    for name, cls in classes.items():
        setattr(torch.optim, name, cls)
