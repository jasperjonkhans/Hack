"""Single-observation linear IDBD; curvature is feature squares, never g**2."""

import math
from collections.abc import Mapping
from numbers import Real

import torch
from torch import Tensor
from torch.optim import Optimizer


def _rate(value, name, positive):
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or (value <= 0 if positive else value < 0)):
        raise ValueError(f'{name} must be a finite {"positive" if positive else "nonnegative"} real scalar')


def _dense_real(value, name):
    if not isinstance(value, Tensor) or value.layout != torch.strided or not value.is_floating_point():
        raise TypeError(f'{name} must be a dense real floating tensor')


def _finite(value, name):
    if not torch.isfinite(value).all().item():
        raise FloatingPointError(f'nonfinite {name}')


class IDBD(Optimizer):
    """Classical linear IDBD with learned log rates and diagonal sensitivity traces.

    ``lr`` initializes beta; it is not a subsequent learning-rate schedule.
    ``meta_lr=0`` gives constant-step LMS. See docs/optimizer.md for scope.
    """

    _guarded = False

    def __init__(self, params, lr=0.01, meta_lr=0.01):
        _rate(lr, 'lr', True)
        _rate(meta_lr, 'meta_lr', False)
        super().__init__(params, dict(lr=lr, meta_lr=meta_lr))

    def add_param_group(self, param_group):
        for key in param_group:
            if key not in {'params', 'lr', 'meta_lr', 'param_names'}:
                raise ValueError(f'unsupported parameter group option: {key}')
        _rate(param_group.get('lr', self.defaults['lr']), 'lr', True)
        _rate(param_group.get('meta_lr', self.defaults['meta_lr']), 'meta_lr', False)
        group = dict(param_group)
        params = group['params']
        if isinstance(params, set):
            raise TypeError('parameters must have a deterministic order, not a set')
        group['params'] = [params] if isinstance(params, Tensor) else list(params)
        for p in group['params']:
            _dense_real(p, 'parameter')
        super().add_param_group(group)

    @torch.no_grad()
    def step(self, closure=None, *, feature_sq: Mapping[Tensor, Tensor]):
        """Update from fixed-example grads and x²; optionally call closure once.

        The closure must zero/recompute gradients and return loss. Gradients are
        neither cleared nor modified by this method. All candidates are checked
        before committing updates (closure side effects are not rolled back).
        """
        loss = None
        if closure is not None:
            with torch.enable_grad():
                loss = closure()
        if not isinstance(feature_sq, Mapping):
            raise TypeError('feature_sq must be a mapping from parameters to x² tensors')
        known = {id(p) for group in self.param_groups for p in group['params']}
        if any(id(p) not in known for p in feature_sq):
            raise ValueError('feature_sq contains an unknown parameter')
        candidates = []
        for group in self.param_groups:
            _rate(group['lr'], 'lr', True)
            _rate(group['meta_lr'], 'meta_lr', False)
            for p in group['params']:
                if p.grad is None:
                    continue
                g = p.grad
                _dense_real(p, 'parameter')
                _dense_real(g, 'gradient')
                if p not in feature_sq:
                    raise ValueError('feature_sq missing a parameter with a gradient')
                c = feature_sq[p]
                _dense_real(c, 'feature_sq')
                for name, value in [('gradient', g), ('feature_sq', c)]:
                    if value.shape != p.shape or value.dtype != p.dtype or value.device != p.device:
                        raise ValueError(f'{name} must match parameter shape, dtype and device')
                    _finite(value, name)
                _finite(p, 'parameter')
                if (c < 0).any().item():
                    raise ValueError('feature_sq must be nonnegative')
                state = self.state.get(p, {})
                beta = state.get('beta', torch.full_like(p, math.log(group['lr'])))
                h = state.get('h', torch.zeros_like(p))
                for name, value in [('beta', beta), ('h', h)]:
                    _dense_real(value, name)
                    if value.shape != p.shape or value.dtype != p.dtype or value.device != p.device:
                        raise ValueError(f'{name} state must match parameter')
                    _finite(value, name)
                b = beta - group['meta_lr'] * g * h
                _finite(b, 'candidate beta')
                candidates.append((p, g, c, b, h))
        shift = 0.
        if self._guarded:
            # Float64 log reductions avoid exp(b) overflow. Host scalar reduction
            # supports groups on different devices, at the cost of synchronization.
            logs = []
            for _, _, c, b, _ in candidates:
                active = c > 0
                if active.any().item():
                    logs.append(torch.logsumexp(b[active].double() + c[active].double().log(), 0).item())
            if logs:
                maximum = max(logs)
                shift = max(0., maximum + math.log(sum(math.exp(v - maximum) for v in logs)))
        updates = []
        for p, g, c, b, h in candidates:
            beta = b - shift
            alpha = beta.exp()
            w = p - alpha * g
            gain = alpha * c
            _finite(gain, 'coordinate gain')
            trace = h * (1 - gain).clamp_min(0) - alpha * g
            for name, value in [('beta', beta), ('alpha', alpha), ('weight', w), ('trace', trace)]:
                _finite(value, name)
            updates.append((p, w, beta, trace))
        for p, w, beta, trace in updates:
            p.copy_(w)
            self.state[p]['beta'] = beta
            self.state[p]['h'] = trace
        return loss


class GuardedIDBD(IDBD):
    """IDBD plus a persistent global aggregate-gain cap of one.

    This is only the aggregate safeguard from Autostep, not full Autostep.
    The inherited trace is heuristic, not the derivative of the guarded map.
    """

    _guarded = True
