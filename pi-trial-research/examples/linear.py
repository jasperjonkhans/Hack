"""One scalar linear observation, not an experiment or performance claim.

From the project root: python -m examples.linear
"""

import torch

from idbd import GuardedIDBD


def main():
    torch.set_num_threads(1)
    w = torch.nn.Parameter(torch.zeros(2, dtype=torch.float64))
    x = torch.tensor([2., -1.], dtype=w.dtype)
    y = torch.tensor(1., dtype=w.dtype)
    optimizer = GuardedIDBD([w], lr=.4, meta_lr=.01)

    def closure():
        optimizer.zero_grad()
        loss = (y - x.dot(w)).square() / 2
        loss.backward()
        return loss

    loss = optimizer.step(closure, feature_sq={w: x.square()})
    print(f'pre-update loss={loss.item():.6f}; weights={w.detach().tolist()}')


if __name__ == '__main__':
    main()
