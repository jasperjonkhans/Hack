import math

import pytest
import torch

torch.set_num_threads(1)

from idbd import IDBD, GuardedIDBD


def reference(w, beta, h, g, c, theta, guarded):
    b = [v - t * gi * hi for v, t, gi, hi in zip(beta, theta, g, h)]
    gain = sum(math.exp(v) * ci for v, ci in zip(b, c))
    shift = math.log(max(1, gain)) if guarded else 0
    beta = [v - shift for v in b]
    alpha = [math.exp(v) for v in beta]
    return ([wi - a * gi for wi, a, gi in zip(w, alpha, g)], beta,
            [hi * max(0, 1 - a * ci) - a * gi for hi, a, ci, gi in zip(h, alpha, c, g)])


@pytest.mark.parametrize('cls', [IDBD, GuardedIDBD])
def test_reference_old_trace_new_alpha(cls):
    p = torch.nn.Parameter(torch.zeros(3, dtype=torch.float64))
    opt = cls([p], lr=.4, meta_lr=.2)
    expected = ([0.] * 3, [math.log(.4)] * 3, [0.] * 3)
    for g, c in [([-2., 1., 0.], [4., 1., 0.]), ([-1., 2., 0.], [1., 4., 0.]),
                 ([0., 0., 0.], [2., 1., 0.])]:
        p.grad = torch.tensor(g, dtype=p.dtype)
        expected = reference(*expected, g, c, [.2] * 3, cls is GuardedIDBD)
        opt.step(feature_sq={p: torch.tensor(c, dtype=p.dtype)})
        for actual, target in zip([p, opt.state[p]['beta'], opt.state[p]['h']], expected):
            torch.testing.assert_close(actual, torch.tensor(target, dtype=p.dtype))


def test_initialization_and_theta_zero_lms():
    p = torch.nn.Parameter(torch.zeros(2, dtype=torch.float64))
    opt = IDBD([p], lr=.1, meta_lr=0)
    assert not opt.state
    for _ in range(3):
        p.grad = torch.tensor([-2., 3.], dtype=p.dtype)
        opt.step(feature_sq={p: torch.tensor([4., 9.], dtype=p.dtype)})
    torch.testing.assert_close(p, torch.tensor([.6, -.9], dtype=p.dtype))
    torch.testing.assert_close(opt.state[p]['beta'], torch.full_like(p, math.log(.1)))


def test_guard_global_groups_absent_and_persistent():
    p, q = [torch.nn.Parameter(torch.zeros(1, dtype=torch.float64)) for _ in range(2)]
    opt = GuardedIDBD([{'params': [p], 'lr': 2.}], meta_lr=0)
    opt.add_param_group({'params': [q], 'lr': 4., 'meta_lr': .3})
    p.grad, q.grad = torch.ones_like(p), torch.zeros_like(q)
    opt.step(feature_sq={p: torch.ones_like(p), q: torch.zeros_like(q)})
    assert opt.state[p]['beta'].exp().item() == pytest.approx(1)
    assert opt.state[q]['beta'].exp().item() == pytest.approx(2)
    p.grad.zero_()
    opt.step(feature_sq={p: torch.zeros_like(p), q: torch.zeros_like(q)})
    assert opt.state[q]['beta'].exp().item() == pytest.approx(2)
    q.grad = None
    old = opt.state[q]['beta'].clone()
    opt.step(feature_sq={p: torch.full_like(p, 4)})
    torch.testing.assert_close(opt.state[q]['beta'], old)


@pytest.mark.parametrize('cls', [IDBD, GuardedIDBD])
@pytest.mark.parametrize('dtype', [torch.float32, torch.float64])
@pytest.mark.parametrize('device', ['cpu', pytest.param('cuda', marks=pytest.mark.skipif(not torch.cuda.is_available(), reason='CUDA unavailable'))])
def test_restore_groups_dtype_device(cls, dtype, device):
    import copy
    p = torch.nn.Parameter(torch.zeros(2, dtype=dtype, device=device))
    q = torch.nn.Parameter(torch.zeros(1, dtype=dtype, device=device))
    opt = cls([{'params': [p], 'lr': .3}, {'params': [q], 'lr': .1, 'meta_lr': .2}])
    def step(optimizer, params):
        for t in params:
            t.grad = torch.full_like(t, -.5)
        optimizer.step(feature_sq={t: torch.ones_like(t) for t in params})
    step(opt, [p, q])
    a, b = [torch.nn.Parameter(t.detach().clone()) for t in [p, q]]
    restored = cls([{'params': [a]}, {'params': [b]}])
    restored.load_state_dict(copy.deepcopy(opt.state_dict()))
    for _ in range(3):
        step(opt, [p, q]); step(restored, [a, b])
    for t, u in [(p, a), (q, b)]:
        torch.testing.assert_close(t, u)
        for key in ['beta', 'h']:
            torch.testing.assert_close(opt.state[t][key], restored.state[u][key])
            assert restored.state[u][key].dtype == dtype
            assert restored.state[u][key].device == u.device
            assert not restored.state[u][key].requires_grad


def test_closure_once_grad_behavior():
    p = torch.nn.Parameter(torch.tensor([0.], dtype=torch.float64))
    opt = IDBD([p], lr=.1)
    calls, losses = [], []
    def closure():
        assert torch.is_grad_enabled()
        calls.append(1)
        opt.zero_grad()
        loss = (2 * p - 1).square().sum() / 2
        loss.backward()
        losses.append(loss)
        return loss
    with torch.no_grad():
        loss = opt.step(closure, feature_sq={p: torch.full_like(p, 4)})
    assert calls == [1] and loss is losses[0]
    torch.testing.assert_close(p.grad, torch.full_like(p, -2))
    assert opt.step(feature_sq={p: torch.full_like(p, 4)}) is None


def test_guard_inactive_equals_classical_and_trace_positive_part():
    ps = [torch.nn.Parameter(torch.zeros(1, dtype=torch.float64)) for _ in range(2)]
    opts = [IDBD([ps[0]], lr=.1), GuardedIDBD([ps[1]], lr=.1)]
    for _ in range(3):
        for p, opt in zip(ps, opts):
            p.grad = torch.ones_like(p)
            opt.step(feature_sq={p: torch.ones_like(p)})
    torch.testing.assert_close(ps[0], ps[1])
    p, opt = ps[0], opts[0]
    p.grad.zero_()
    opt.step(feature_sq={p: torch.full_like(p, 100)})
    assert opt.state[p]['h'].item() == 0


@pytest.mark.parametrize('bad', ['missing', 'shape', 'dtype', 'negative', 'nan', 'sparse', 'complex', 'unknown', 'mapping', 'gradient'])
def test_validation_atomic_across_groups(bad):
    p, q = [torch.nn.Parameter(torch.zeros(2)) for _ in range(2)]
    opt = IDBD([{'params': [p]}, {'params': [q]}])
    p.grad, q.grad = torch.ones_like(p), torch.ones_like(q)
    c = {p: torch.ones_like(p), q: torch.ones_like(q)}
    if bad == 'missing': del c[q]
    if bad == 'shape': c[q] = torch.ones(1)
    if bad == 'dtype': c[q] = c[q].double()
    if bad == 'negative': c[q][0] = -1
    if bad == 'nan': c[q][0] = float('nan')
    if bad == 'sparse': c[q] = c[q].to_sparse()
    if bad == 'complex': c[q] = c[q].to(torch.complex64)
    if bad == 'unknown': c[torch.zeros(2)] = torch.ones(2)
    if bad == 'mapping': c = []
    if bad == 'gradient': q.grad = q.grad.to_sparse()
    with pytest.raises((ValueError, TypeError, FloatingPointError)):
        opt.step(feature_sq=c)
    assert not opt.state
    assert torch.count_nonzero(p) == torch.count_nonzero(q) == 0


@pytest.mark.parametrize('kwargs', [{'lr': 0}, {'lr': float('inf')}, {'meta_lr': -1}, {'meta_lr': float('nan')}, {'lr': torch.tensor(.1)}])
def test_invalid_rates(kwargs):
    with pytest.raises(ValueError): IDBD([torch.zeros(1)], **kwargs)


def test_unsupported_and_nonfinite():
    with pytest.raises(TypeError): IDBD([torch.zeros(1, dtype=torch.complex64)])
    with pytest.raises(ValueError): IDBD([{'params': [torch.zeros(1)], 'weight_decay': .1}])
    p = torch.nn.Parameter(torch.zeros(1))
    opt = IDBD([p])
    p.grad = torch.ones_like(p)
    opt.step(feature_sq={p: torch.ones_like(p)})
    opt.state[p]['beta'].fill_(1000)
    old = p.clone()
    with pytest.raises(FloatingPointError): opt.step(feature_sq={p: torch.ones_like(p)})
    torch.testing.assert_close(p, old)
    assert opt.state[p]['beta'].item() == 1000


def test_guard_logspace_large_candidate():
    p = torch.nn.Parameter(torch.zeros(2, dtype=torch.float64))
    opt = GuardedIDBD([p])
    opt.state[p].update(beta=torch.full_like(p, 1000), h=torch.zeros_like(p))
    p.grad = torch.ones_like(p)
    opt.step(feature_sq={p: torch.ones_like(p)})
    torch.testing.assert_close(p, torch.full_like(p, -.5))


def test_registration_subprocess():
    import subprocess
    import sys
    code = '''
import torch
assert not hasattr(torch.optim, 'IDBD')
import idbd
assert not hasattr(torch.optim, 'IDBD')
torch.optim.GuardedIDBD = object()
try:
    idbd.register_torch_optimizer()
except RuntimeError:
    pass
else:
    raise AssertionError('expected conflict')
assert not hasattr(torch.optim, 'IDBD')
del torch.optim.GuardedIDBD
idbd.register_torch_optimizer()
idbd.register_torch_optimizer()
assert torch.optim.IDBD is idbd.IDBD
assert torch.optim.GuardedIDBD is idbd.GuardedIDBD
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_nonfinite_trace_product_reported():
    p = torch.nn.Parameter(torch.zeros(1, dtype=torch.float32))
    opt = IDBD([p], lr=1e30)
    p.grad = torch.zeros_like(p)
    with pytest.raises(FloatingPointError):
        opt.step(feature_sq={p: torch.full_like(p, 1e30)})
    assert not opt.state


def test_guard_multiple_active_groups_reference_and_residual():
    p = torch.nn.Parameter(torch.zeros((1, 2), dtype=torch.float64))
    q = torch.nn.Parameter(torch.zeros(1, dtype=torch.float64))
    opt = GuardedIDBD([{'params': [p], 'lr': .4, 'meta_lr': .1},
                       {'params': [q], 'lr': .8, 'meta_lr': .3}])
    expected = ([0.] * 3, [math.log(.4)] * 2 + [math.log(.8)], [0.] * 3)
    x = torch.tensor([2., 1., -3.], dtype=p.dtype)
    for y in [1., 2., -1.]:
        before = torch.cat([p.flatten(), q]).detach()
        e = y - x.dot(before)
        g, c = -e * x, x.square()
        p.grad, q.grad = g[:2].reshape_as(p), g[2:]
        expected = reference(*expected, g.tolist(), c.tolist(), [.1, .1, .3], True)
        opt.step(feature_sq={p: c[:2].reshape_as(p), q: c[2:]})
        after = torch.cat([p.flatten(), q]).detach()
        torch.testing.assert_close(after, torch.tensor(expected[0], dtype=p.dtype))
        alpha = torch.cat([opt.state[p]['beta'].flatten(), opt.state[q]['beta']]).exp()
        gain = alpha.dot(c)
        assert gain <= 1 + 1e-14
        torch.testing.assert_close(y - x.dot(after), (1 - gain) * e)


def test_example_and_package_metadata(capsys):
    import tomllib
    from pathlib import Path
    from examples.linear import main
    metadata = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
    assert metadata['project']['name'] == 'linear-idbd'
    main()
    assert 'pre-update loss=0.500000' in capsys.readouterr().out


def test_validation_preserves_existing_state():
    import copy
    p, q = [torch.nn.Parameter(torch.zeros(1)) for _ in range(2)]
    opt = IDBD([p, q])
    for t in [p, q]: t.grad = torch.ones_like(t)
    opt.step(feature_sq={p: torch.ones_like(p), q: torch.ones_like(q)})
    old = copy.deepcopy(opt.state_dict())
    weights = [t.clone() for t in [p, q]]
    with pytest.raises(ValueError):
        opt.step(feature_sq={p: torch.ones_like(p), q: -torch.ones_like(q)})
    for t, w in zip([p, q], weights): torch.testing.assert_close(t, w)
    for index, state in old['state'].items():
        for key, value in state.items():
            torch.testing.assert_close(opt.state_dict()['state'][index][key], value)
