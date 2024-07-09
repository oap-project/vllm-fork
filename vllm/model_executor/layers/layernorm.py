"""Custom normalization layers."""
from typing import Optional, Tuple, Union

import torch
import torch.nn as nn

from vllm import _custom_ops as ops
from vllm.utils import is_hpu

if is_hpu():
    try:
        from habana_frameworks.torch.hpex.normalization import FusedRMSNorm as FusedRMSNorm
    except ImportError:
        print("Not using HPU fused kernel for RMSNorm")
        FusedRMSNorm = None

class RMSNorm(nn.Module):
    """Root mean square normalization.

    Computes x -> w * x / sqrt(E[x^2] + eps) where w is the learned weight.
    Refer to https://arxiv.org/abs/1910.07467
    """

    def __init__(
        self,
        hidden_size: int,
        eps: float = 1e-6,
    ) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(hidden_size))
        self.variance_epsilon = eps

    def _forward(
        self,
        x: torch.Tensor,
        residual: Optional[torch.Tensor] = None,
    ) -> Union[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]:
        """PyTorch-native implementation equivalent to forward()."""
        orig_dtype = x.dtype
        x = x.to(torch.float32)
        if residual is not None:
            x = x + residual.to(torch.float32)
            residual = x.to(orig_dtype)

        variance = x.pow(2).mean(dim=-1, keepdim=True)
        x = x * torch.rsqrt(variance + self.variance_epsilon)
        x = x.to(orig_dtype) * self.weight
        if residual is None:
            return x
        else:
            return x, residual

    def forward(
        self,
        x: torch.Tensor,
        residual: Optional[torch.Tensor] = None,
    ) -> Union[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]:
        if residual is not None:
            if x.device.type == "hpu" and FusedRMSNorm:
                orig_shape = x.shape
                residual += x.view(residual.shape)
                # Note: FusedRMSNorm requires 3D tensors as inputs
                x = FusedRMSNorm.apply(residual, self.weight, self.variance_epsilon)
                return x.view(orig_shape), residual
            ops.fused_add_rms_norm(
                x,
                residual,
                self.weight.data,
                self.variance_epsilon,
            )
            return x, residual
        if x.device.type == "hpu" and FusedRMSNorm:
            x = FusedRMSNorm.apply(x, self.weight, self.variance_epsilon)
            return x
        out = torch.empty_like(x)
        ops.rms_norm(
            out,
            x,
            self.weight.data,
            self.variance_epsilon,
        )
        return out

    def extra_repr(self) -> str:
        s = f"hidden_size={self.weight.data.size(0)}"
        s += f", eps={self.variance_epsilon}"
        return s
