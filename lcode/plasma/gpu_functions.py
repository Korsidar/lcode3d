# Copyright (c) 2016-2017 LCODE team <team@lcode.info>.

# LCODE is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# LCODE is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with LCODE.  If not, see <http://www.gnu.org/licenses/>.

from math import sqrt, floor

import numpy as np

import numba
import numba.cuda

import pyculib.fft

RoJ_dtype = np.dtype([
    ('ro', np.double),
    ('jz', np.double),
    ('jx', np.double),
    ('jy', np.double),
], align=False)


@numba.cuda.jit
def zerofill_kernel(arr1d):
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, arr1d.size, stride):
        arr1d[k] = 0


# TODO: write a version fused with averaging
# TODO: fuse with moving?
@numba.cuda.jit
def interpolate_kernel(xs, ys, Ex, Ey, Ez, Bx, By, Bz,
                       grid_step_size, grid_steps,
                       Exs, Eys, Ezs, Bxs, Bys, Bzs):
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, xs.size, stride):
        x_h = xs[k] / grid_step_size + .5
        y_h = ys[k] / grid_step_size + .5
        i = int(floor(x_h) + grid_steps // 2)
        j = int(floor(y_h) + grid_steps // 2)
        x_loc = x_h - floor(x_h) - .5  # centered to -.5 to 5, not 0 to 1 because
        y_loc = y_h - floor(y_h) - .5  # the latter formulas use offset from cell center

        fx1 = .75 - x_loc**2
        fy1 = .75 - y_loc**2
        fx2 = .5 + x_loc
        fy2 = .5 + y_loc
        fx3 = .5 - x_loc
        fy3 = .5 - y_loc

        # TODO: use the same names in deposition?
        w00 = fx1 * fy1
        wP0 = fx2**2 * (fy1 / 2)
        w0P = fy2**2 * (fx1 / 2)
        wPP = fx2**2 * (fy2**2 / 4)
        wM0 = fx3**2 * (fy1 / 2)
        w0M = fy3**2 * (fx1 / 2)
        wMM = fx3**2 * (fy3**2 / 4)
        wMP = fx3**2 * (fy2**2 / 4)
        wPM = fx2**2 * (fy3**2 / 4)

        Exs[k] = (
            Ex[i + 0, j + 0] * w00 +
            Ex[i + 1, j + 0] * wP0 +
            Ex[i + 0, j + 1] * w0P +
            Ex[i + 1, j + 1] * wPP +
            Ex[i - 1, j + 0] * wM0 +
            Ex[i + 0, j - 1] * w0M +
            Ex[i - 1, j - 1] * wMM +
            Ex[i - 1, j + 1] * wMP +
            Ex[i + 1, j - 1] * wPM
        )

        Eys[k] = (
            Ey[i + 0, j + 0] * w00 +
            Ey[i + 1, j + 0] * wP0 +
            Ey[i + 0, j + 1] * w0P +
            Ey[i + 1, j + 1] * wPP +
            Ey[i - 1, j + 0] * wM0 +
            Ey[i + 0, j - 1] * w0M +
            Ey[i - 1, j - 1] * wMM +
            Ey[i - 1, j + 1] * wMP +
            Ey[i + 1, j - 1] * wPM
        )

        Ezs[k] = (
            Ez[i + 0, j + 0] * w00 +
            Ez[i + 1, j + 0] * wP0 +
            Ez[i + 0, j + 1] * w0P +
            Ez[i + 1, j + 1] * wPP +
            Ez[i - 1, j + 0] * wM0 +
            Ez[i + 0, j - 1] * w0M +
            Ez[i - 1, j - 1] * wMM +
            Ez[i - 1, j + 1] * wMP +
            Ez[i + 1, j - 1] * wPM
        )

        Bxs[k] = (
            Bx[i + 0, j + 0] * w00 +
            Bx[i + 1, j + 0] * wP0 +
            Bx[i + 0, j + 1] * w0P +
            Bx[i + 1, j + 1] * wPP +
            Bx[i - 1, j + 0] * wM0 +
            Bx[i + 0, j - 1] * w0M +
            Bx[i - 1, j - 1] * wMM +
            Bx[i - 1, j + 1] * wMP +
            Bx[i + 1, j - 1] * wPM
        )

        Bys[k] = (
            By[i + 0, j + 0] * w00 +
            By[i + 1, j + 0] * wP0 +
            By[i + 0, j + 1] * w0P +
            By[i + 1, j + 1] * wPP +
            By[i - 1, j + 0] * wM0 +
            By[i + 0, j - 1] * w0M +
            By[i - 1, j - 1] * wMM +
            By[i - 1, j + 1] * wMP +
            By[i + 1, j - 1] * wPM
        )

        Bzs[k] = 0  # Bz = 0 for now


# TODO: add ro_initial the last, as it is comparatively large (float tricks)?
@numba.cuda.jit
def roj_init_kernel(ro, jx, jy, jz, ro_initial):
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, ro.size, stride):
        ro[k] = ro_initial[k]
        jx[k] = jy[k] = jz[k] = 0


@numba.cuda.jit
def deposit_kernel(n_dim, h,
                   c_x, c_y, c_m, c_q, c_p_x, c_p_y, c_p_z,  # coarse
                   A_weights, B_weights, C_weights, D_weights,
                   indices_prev, indices_next, smallness_factor,
                   out_ro, out_jx, out_jy, out_jz):
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for pk in range(index, A_weights.size, stride):
        pi, pj = pk // A_weights.shape[0], pk % A_weights.shape[0]

        px, nx = indices_prev[pi], indices_next[pi]
        py, ny = indices_prev[pj], indices_next[pj]

        A = A_weights[pi, pj]
        B = B_weights[pi, pj]
        C = C_weights[pi, pj]
        D = D_weights[pi, pj]

        x = A * c_x[px, py] + B * c_x[nx, py] + C * c_x[px, ny] + D * c_x[nx, ny]
        y = A * c_y[px, py] + B * c_y[nx, py] + C * c_y[px, ny] + D * c_y[nx, ny]
        m = A * c_m[px, py] + B * c_m[nx, py] + C * c_m[px, ny] + D * c_m[nx, ny]
        q = A * c_q[px, py] + B * c_q[nx, py] + C * c_q[px, ny] + D * c_q[nx, ny]
        p_x = A * c_p_x[px, py] + B * c_p_x[nx, py] + C * c_p_x[px, ny] + D * c_p_x[nx, ny]
        p_y = A * c_p_y[px, py] + B * c_p_y[nx, py] + C * c_p_y[px, ny] + D * c_p_y[nx, ny]
        p_z = A * c_p_z[px, py] + B * c_p_z[nx, py] + C * c_p_z[px, ny] + D * c_p_z[nx, ny]
        m *= smallness_factor
        q *= smallness_factor
        p_x *= smallness_factor
        p_y *= smallness_factor
        p_z *= smallness_factor

        m_sq = m**2
        p_x_sq = p_x**2
        p_y_sq = p_y**2
        p_z_sq = p_z**2
        gamma_m = sqrt(m_sq + p_x_sq + p_y_sq + p_z_sq)
        dro = q / (1 - p_z / gamma_m)
        djx = p_x * (dro / gamma_m)
        djy = p_y * (dro / gamma_m)
        djz = p_z * (dro / gamma_m)

        x_h = x / h + .5
        y_h = y / h + .5
        i = int(floor(x_h)) + n_dim // 2
        j = int(floor(y_h)) + n_dim // 2
        x_loc = x_h - floor(x_h) - 0.5
        y_loc = y_h - floor(y_h) - 0.5

        fx1 = .75 - x_loc**2
        fy1 = .75 - y_loc**2
        fx2 = .5  + x_loc
        fy2 = .5  + y_loc
        fx3 = .5  - x_loc
        fy3 = .5  - y_loc

        fx2_sq = fx2**2
        fy2_sq = fy2**2
        fx3_sq = fx3**2
        fy3_sq = fy3**2

        # atomic +=, thread-safe
        numba.cuda.atomic.add(out_ro, (i + 0, j + 0), dro * (fx1 * fy1))
        numba.cuda.atomic.add(out_ro, (i + 1, j + 0), dro * (fx2_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_ro, (i + 0, j + 1), dro * (fy2_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_ro, (i + 1, j + 1), dro * (fx2_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_ro, (i - 1, j + 0), dro * (fx3_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_ro, (i + 0, j - 1), dro * (fy3_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_ro, (i - 1, j - 1), dro * (fx3_sq * (fy3_sq / 4)))
        numba.cuda.atomic.add(out_ro, (i - 1, j + 1), dro * (fx3_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_ro, (i + 1, j - 1), dro * (fx2_sq * (fy3_sq / 4)))

        numba.cuda.atomic.add(out_jx, (i + 0, j + 0), djx * (fx1 * fy1))
        numba.cuda.atomic.add(out_jx, (i + 1, j + 0), djx * (fx2_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_jx, (i + 0, j + 1), djx * (fy2_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_jx, (i + 1, j + 1), djx * (fx2_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_jx, (i - 1, j + 0), djx * (fx3_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_jx, (i + 0, j - 1), djx * (fy3_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_jx, (i - 1, j - 1), djx * (fx3_sq * (fy3_sq / 4)))
        numba.cuda.atomic.add(out_jx, (i - 1, j + 1), djx * (fx3_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_jx, (i + 1, j - 1), djx * (fx2_sq * (fy3_sq / 4)))

        numba.cuda.atomic.add(out_jy, (i + 0, j + 0), djy * (fx1 * fy1))
        numba.cuda.atomic.add(out_jy, (i + 1, j + 0), djy * (fx2_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_jy, (i + 0, j + 1), djy * (fy2_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_jy, (i + 1, j + 1), djy * (fx2_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_jy, (i - 1, j + 0), djy * (fx3_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_jy, (i + 0, j - 1), djy * (fy3_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_jy, (i - 1, j - 1), djy * (fx3_sq * (fy3_sq / 4)))
        numba.cuda.atomic.add(out_jy, (i - 1, j + 1), djy * (fx3_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_jy, (i + 1, j - 1), djy * (fx2_sq * (fy3_sq / 4)))

        numba.cuda.atomic.add(out_jz, (i + 0, j + 0), djz * (fx1 * fy1))
        numba.cuda.atomic.add(out_jz, (i + 1, j + 0), djz * (fx2_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_jz, (i + 0, j + 1), djz * (fy2_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_jz, (i + 1, j + 1), djz * (fx2_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_jz, (i - 1, j + 0), djz * (fx3_sq * (fy1 / 2)))
        numba.cuda.atomic.add(out_jz, (i + 0, j - 1), djz * (fy3_sq * (fx1 / 2)))
        numba.cuda.atomic.add(out_jz, (i - 1, j - 1), djz * (fx3_sq * (fy3_sq / 4)))
        numba.cuda.atomic.add(out_jz, (i - 1, j + 1), djz * (fx3_sq * (fy2_sq / 4)))
        numba.cuda.atomic.add(out_jz, (i + 1, j - 1), djz * (fx2_sq * (fy3_sq / 4)))
    #numba.cuda.syncthreads()


@numba.cuda.jit
def calculate_RHS_Ex_Ey_Bx_By_kernel(Ex_sub, Ey_sub, Bx_sub, By_sub,
                                     beam_ro, ro, jx, jx_prev, jy, jy_prev, jz,
                                     grid_step_size, xi_step_size,
                                     subtraction_trick,
                                     Ex_dct1_in, Ey_dct1_in,
                                     Bx_dct1_in, By_dct1_in):
    N = ro.shape[0]
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, ro.size, stride):
        i, j = k // N, k % N

        dro_dx = (((+ro[i + 1, j] + beam_ro[i + 1, j]
                    -ro[i - 1, j] - beam_ro[i - 1, j])
                  ) / (2 * grid_step_size)  # - ?
                  if 0 < i < N - 1 else 0)
        dro_dy = (((+ro[i, j + 1] + beam_ro[i, j + 1]
                    -ro[i, j - 1] - beam_ro[i, j - 1])
                  ) / (2 * grid_step_size)  # - ?
                  if 0 < j < N - 1 else 0)
        djz_dx = (((+jz[i + 1, j] + beam_ro[i + 1, j]
                    -jz[i - 1, j] - beam_ro[i - 1, j])
                  ) / (2 * grid_step_size)  # - ?
                  if 0 < i < N - 1 else 0)
        djz_dy = (((+jz[i, j + 1] + beam_ro[i, j + 1]
                    -jz[i, j - 1] - beam_ro[i, j - 1])
                  ) / (2 * grid_step_size)  # - ?
                  if 0 < j < N - 1 else 0)
        djx_dxi = (jx_prev[i, j] - jx[i, j]) / xi_step_size               # - ?
        djy_dxi = (jy_prev[i, j] - jy[i, j]) / xi_step_size               # - ?

        Ex_rhs = -((dro_dx - djx_dxi) - Ex_sub[i, j] * subtraction_trick)
        Ey_rhs = -((dro_dy - djy_dxi) - Ey_sub[i, j] * subtraction_trick)
        Bx_rhs = +((djz_dy - djy_dxi) + Bx_sub[i, j] * subtraction_trick)
        By_rhs = -((djz_dx - djx_dxi) - By_sub[i, j] * subtraction_trick)
        Ex_dct1_in[j, i] = Ex_rhs
        Ey_dct1_in[i, j] = Ey_rhs
        Bx_dct1_in[i, j] = Bx_rhs
        By_dct1_in[j, i] = By_rhs
        # symmetrically pad dct1_in to apply DCT-via-FFT later
        ii = max(i, 1)  # avoid writing to dct_in[:, 2 * N - 2], w/o branching
        jj = max(j, 1)
        Ex_dct1_in[j, 2 * N - 2 - ii] = Ex_rhs
        Ey_dct1_in[i, 2 * N - 2 - jj] = Ey_rhs
        Bx_dct1_in[i, 2 * N - 2 - jj] = Bx_rhs
        By_dct1_in[j, 2 * N - 2 - ii] = By_rhs

        # applying non-zero boundary conditions to the RHS would be:
        # for i in range(self.N):
            # rhs_fixed[i, 0] += top[i] * (2 / self.grid_step_size)
            # rhs_fixed[i, self.N - 1] += bot[i] * (2 / self.grid_step_size)
            ## rhs_fixed[0, i] = rhs_fixed[self.N - 1, i] = 0
            ### changes nothing, as there's a particle-free padding zone?


@numba.cuda.jit
def mid_dct_transform(Ex_dct1_out, Ex_dct2_in,
                      Ey_dct1_out, Ey_dct2_in,
                      Bx_dct1_out, Bx_dct2_in,
                      By_dct1_out, By_dct2_in,
                      Ex_bet, Ey_bet, Bx_bet, By_bet,
                      alf, mul):
    N = Ex_dct1_out.shape[0]
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x

    # Solve tridiagonal matrix equation for each spectral column with Thomas method:
    # A @ tmp_2[k, :] = tmp_1[k, :]
    # A has -1 on superdiagonal, -1 on subdiagonal and aa[k] at the main diagonal
    # The edge elements of each column are forced to 0!
    for i in range(index, N, stride):
        Ex_bet[i, 0] = Ey_bet[i, 0] = Bx_bet[i, 0] = By_bet[i, 0] = 0
        for j in range(1, N - 1):
            # Note the transposition for dct1_out!
            Ex_bet[i, j + 1] = (mul * Ex_dct1_out[j, i].real + Ex_bet[i, j]) * alf[i, j + 1]
            Ey_bet[i, j + 1] = (mul * Ey_dct1_out[j, i].real + Ey_bet[i, j]) * alf[i, j + 1]
            Bx_bet[i, j + 1] = (mul * Bx_dct1_out[j, i].real + Bx_bet[i, j]) * alf[i, j + 1]
            By_bet[i, j + 1] = (mul * By_dct1_out[j, i].real + By_bet[i, j]) * alf[i, j + 1]
        # Note the transposition for dct2_in!
        # TODO: it can be set once only? Maybe we can comment that out then?
        Ex_dct2_in[N - 1, i] = Ey_dct2_in[N - 1, i] = 0  # Note the forced zero
        Bx_dct2_in[N - 1, i] = By_dct2_in[N - 1, i] = 0
        for j in range(N - 2, 0 - 1, -1):
            Ex_dct2_in[j, i] = alf[i, j + 1] * Ex_dct2_in[j + 1, i] + Ex_bet[i, j + 1]
            Ey_dct2_in[j, i] = alf[i, j + 1] * Ey_dct2_in[j + 1, i] + Ey_bet[i, j + 1]
            Bx_dct2_in[j, i] = alf[i, j + 1] * Bx_dct2_in[j + 1, i] + Bx_bet[i, j + 1]
            By_dct2_in[j, i] = alf[i, j + 1] * By_dct2_in[j + 1, i] + By_bet[i, j + 1]
            # also symmetrical-fill the array in preparation for a second DCT
            ii = max(i, 1)  # avoid writing to dct_in[:, 2 * N - 2], w/o branching
            Ex_dct2_in[j, 2 * N - 2 - ii] = Ex_dct2_in[j, ii]
            Ey_dct2_in[j, 2 * N - 2 - ii] = Ey_dct2_in[j, ii]
            Bx_dct2_in[j, 2 * N - 2 - ii] = Bx_dct2_in[j, ii]
            By_dct2_in[j, 2 * N - 2 - ii] = By_dct2_in[j, ii]
        # dct2_in[:, 0] == 0  # happens by itself


@numba.cuda.jit
def unpack_Ex_Ey_Bx_By_fields_kernel(Ex_dct2_out, Ey_dct2_out,
                                     Bx_dct2_out, By_dct2_out,
                                     Ex, Ey, Bx, By):
    N = Ex.shape[0]
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, Ex.size, stride):
        i, j = k // N, k % N
        Ex[i, j] = Ex_dct2_out[j, i].real
        Ey[i, j] = Ey_dct2_out[i, j].real
        Bx[i, j] = Bx_dct2_out[i, j].real
        By[i, j] = By_dct2_out[j, i].real


@numba.cuda.jit
def calculate_RHS_Ez_kernel(jx, jy, grid_step_size, Ez_dst1_in):
    N = jx.shape[0]
    Ns = N - 2
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, Ns**2, stride):
        i0, j0 = k // Ns, k % Ns
        i, j = i0 + 1, j0 + 1

        djx_dx = (jx[i + 1, j] - jx[i - 1, j]) / (2 * grid_step_size)  # - ?
        djy_dy = (jy[i, j + 1] - jy[i, j - 1]) / (2 * grid_step_size)  # - ?

        Ez_rhs = -(djx_dx + djy_dy)
        Ez_dst1_in[i0, j0 + 1] = Ez_rhs
        # anti-symmetrically pad dct1_in to apply DCT-via-FFT later
        Ez_dst1_in[i0, 2 * Ns + 1 - j0] = -Ez_rhs

@numba.cuda.jit
def mid_dst_transform(Ez_dst1_out, Ez_dst2_in,
                      Ez_bet, Ez_alf, mul):
    Ns = Ez_dst1_out.shape[0]  # == N - 2
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x

    # Solve tridiagonal matrix equation for each spectral column with Thomas method:
    # A @ tmp_2[k, :] = tmp_1[k, :]
    # A has -1 on superdiagonal, -1 on subdiagonal and aa[i] at the main diagonal
    for i in range(index, Ns, stride):
        Ez_bet[i, 0] = 0
        for j in range(Ns):
            # Note the transposition for dst1_out!
            Ez_bet[i, j + 1] = (mul * -Ez_dst1_out[j, i + 1].imag + Ez_bet[i, j]) * Ez_alf[i, j + 1]
        # Note the transposition for dct2_in!
        Ez_dst2_in[Ns - 1, i + 1] = 0 + Ez_bet[i, Ns]  # 0 = Ez_dst2_in[i, Ns] (fake)
        Ez_dst2_in[Ns - 1, 2 * Ns + 1 - i] = -Ez_dst2_in[Ns - 1, i + 1]
        for j in range(Ns - 2, 0 - 1, -1):
            Ez_dst2_in[j, i + 1] = Ez_alf[i, j + 1] * Ez_dst2_in[j + 1, i + 1] + Ez_bet[i, j + 1]
            # anti-symmetrically pad dct1_in to apply DCT-via-FFT later
            Ez_dst2_in[j, 2 * Ns + 1 - i] = -Ez_dst2_in[j, i + 1]


@numba.cuda.jit
def unpack_Ez_kernel(Ez_dst2_out, Ez,
                     Ez_dst1_in, Ez_dst1_out, Ez_dst2_in):
    N = Ez.shape[0]
    Ns = N - 2
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, Ns**2, stride):
        i0, j0 = k // Ns, k % Ns
        i, j = i0 + 1, j0 + 1
        Ez[i, j] = -Ez_dst2_out[i0, j0 + 1].imag


# TODO: try averaging many arrays at once, * .5,
#       maybe even combining field arrays into one
@numba.cuda.jit
def average_arrays_kernel(arr1, arr2, out):
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, out.size, stride):
        out[k] = (arr1[k] + arr2[k]) / 2


#cpdef void move_simple_fast_(PlasmaSolverConfig config,
#                             np.ndarray[plasma_particle.t] plasma_particles,
#                             double dxiP,
#                             np.ndarray[plasma_particle.t] out_plasma,
#                             ):
#    cdef long k
#    cdef double gamma_m
#    cdef plasma_particle.t p
#
#    # for p in plasma_particles: indexed for performance
#    for k in cython.parallel.prange(plasma_particles.shape[0],
#                                    nogil=True, num_threads=config.threads):
#        p = plasma_particles[k]
#
#        gamma_m = sqrt(p.m**2 + p.p[0]**2 + p.p[1]**2 + p.p[2]**2)
#        p.x += p.p[1] / (gamma_m - p.p[0]) * dxiP
#        p.y += p.p[2] / (gamma_m - p.p[0]) * dxiP
#
#        if p.x > config.particle_boundary:
#            p.x = +2 * config.particle_boundary - p.x
#            p.p[1] *= -1
#        if p.x < -config.particle_boundary:
#            p.x = -2 * config.particle_boundary - p.x
#            p.p[1] *= -1
#        if p.y > config.particle_boundary:
#            p.y = +2 * config.particle_boundary - p.y
#            p.p[2] *= -1
#        if p.y < -config.particle_boundary:
#            p.y = -2 * config.particle_boundary - p.y
#            p.p[2] *= -1
#
#        out_plasma[k] = p


@numba.cuda.jit
def move_smart_kernel(xi_step_size, particle_boundary,
                      ms, qs, old_x, old_y, old_px, old_py, old_pz,
                      Exs, Eys, Ezs, Bxs, Bys, Bzs,
                      new_x, new_y, new_px, new_py, new_pz):
    index = numba.cuda.grid(1)
    stride = numba.cuda.blockDim.x * numba.cuda.gridDim.x
    for k in range(index, ms.size, stride):
        m, q = ms[k], qs[k]
        x, y, px, py, pz = old_x[k], old_y[k], old_px[k], old_py[k], old_pz[k]

        gamma_m = sqrt(m**2 + pz**2 + px**2 + py**2)
        vx, vy, vz = px / gamma_m, py / gamma_m, pz / gamma_m
        factor_1 = q * xi_step_size / (1 - pz / gamma_m)
        dpx = factor_1 * (Exs[k] + vy * Bzs[k] - vz * Bys[k])
        dpy = factor_1 * (Eys[k] - vx * Bzs[k] + vz * Bxs[k])
        dpz = factor_1 * (Ezs[k] + vx * Bys[k] - vy * Bxs[k])
        px, py, pz = old_px[k] + dpx / 2, old_py[k] + dpy / 2, old_pz[k] + dpz / 2

        gamma_m = sqrt(m**2 + pz**2 + px**2 + py**2)
        vx, vy, vz = px / gamma_m, py / gamma_m, pz / gamma_m
        factor_1 = q * xi_step_size / (1 - pz / gamma_m)
        dpx = factor_1 * (Exs[k] + vy * Bzs[k] - vz * Bys[k])
        dpy = factor_1 * (Eys[k] - vx * Bzs[k] + vz * Bxs[k])
        dpz = factor_1 * (Ezs[k] + vx * Bys[k] - vy * Bxs[k])
        px, py, pz = old_px[k] + dpx / 2, old_py[k] + dpy / 2, old_pz[k] + dpz / 2

        gamma_m = sqrt(m**2 + pz**2 + px**2 + py**2)

        x += px / (gamma_m - pz) * xi_step_size
        y += py / (gamma_m - pz) * xi_step_size

        px, py, pz = old_px[k] + dpx, old_py[k] + dpy, old_pz[k] + dpz

        # TODO: avoid branching?
        if x > +particle_boundary:
            x = +2 * particle_boundary - x
            px = -px
        if x < -particle_boundary:
            x = -2 * particle_boundary - x
            px = -px
        if y > +particle_boundary:
            y = +2 * particle_boundary - y
            py = -py
        if y < -particle_boundary:
            y = -2 * particle_boundary - y
            py = -py

        new_x[k], new_y[k], new_px[k], new_py[k], new_pz[k] = x, y, px, py, pz


class GPUMonolith:
    cfg = (19, 192)  # empirical guess for a GTX 1070 Ti

    def __init__(self, config):
        self._Nc = Nc = int(sqrt(config.plasma.size))
        assert Nc**2 == config.plasma.size

        self.grid_steps = N = config.grid_steps
        self.xi_step_size = config.xi_step_size
        self.grid_step_size = config.window_width / config.grid_steps
        self.subtraction_trick = config.field_solver_subtraction_trick
        self.particle_boundary = (
            +config.window_width / 2
            -config.plasma_padding * self.grid_step_size
        )

        self.virtplasma_smallness_factor = 1 / config.virtualize.ratio

        self._m = numba.cuda.device_array((Nc, Nc))
        self._q = numba.cuda.device_array((Nc, Nc))
        self._x = numba.cuda.device_array((Nc, Nc))
        self._y = numba.cuda.device_array((Nc, Nc))
        self._px = numba.cuda.device_array((Nc, Nc))
        self._py = numba.cuda.device_array((Nc, Nc))
        self._pz = numba.cuda.device_array((Nc, Nc))

        self._new_x = numba.cuda.device_array((Nc, Nc))
        self._new_y = numba.cuda.device_array((Nc, Nc))
        self._new_px = numba.cuda.device_array((Nc, Nc))
        self._new_py = numba.cuda.device_array((Nc, Nc))
        self._new_pz = numba.cuda.device_array((Nc, Nc))

        self._halfstep_x = numba.cuda.device_array((Nc, Nc))
        self._halfstep_y = numba.cuda.device_array((Nc, Nc))

        self._Exs = numba.cuda.device_array((Nc, Nc))
        self._Eys = numba.cuda.device_array((Nc, Nc))
        self._Ezs = numba.cuda.device_array((Nc, Nc))
        self._Bxs = numba.cuda.device_array((Nc, Nc))
        self._Bys = numba.cuda.device_array((Nc, Nc))
        self._Bzs = numba.cuda.device_array((Nc, Nc))

        self._A_weights = numba.cuda.to_device(config.virtualize.A_weights)
        self._B_weights = numba.cuda.to_device(config.virtualize.B_weights)
        self._C_weights = numba.cuda.to_device(config.virtualize.C_weights)
        self._D_weights = numba.cuda.to_device(config.virtualize.D_weights)
        self._indices_prev = numba.cuda.to_device(config.virtualize.indices_prev)
        self._indices_next = numba.cuda.to_device(config.virtualize.indices_next)

        # Arrays for mixed boundary conditions solver
        # * diagonal matrix elements (used in the next one)
        aa = 2 + 4 * np.sin(np.arange(0, N) * np.pi / (2 * (N - 1)))**2
        if self.subtraction_trick:
            aa += self.grid_step_size**2 * self.subtraction_trick
        alf = np.zeros((N, N + 1))
        # * precalculated internal coefficients for tridiagonal solving
        for i in range(1, N):
            alf[:, i + 1] = 1 / (aa - alf[:, i])
        self._mix_alf = numba.cuda.to_device(alf)
        # * scratchpad arrays for mixed boundary conditions solver
        self._Ex_bet = numba.cuda.device_array((N, N))
        self._Ey_bet = numba.cuda.device_array((N, N))
        self._Bx_bet = numba.cuda.device_array((N, N))
        self._By_bet = numba.cuda.device_array((N, N))

        # Arrays for Dirichlet boundary conditions solver
        # * diagonal matrix elements (used in the next one)
        Ez_a = 2 + 4 * np.sin(np.arange(1, N - 1) * np.pi / (2 * (N - 1)))**2
        #  +  h**2  # only used with xi derivatives
        # * precalculated internal coefficients for tridiagonal solving
        Ez_alf = np.zeros((N - 2, N - 1))
        Ez_alf[:, 0] = 0
        for k in range(N - 2):
            for i in range(N - 2):
                Ez_alf[k, i + 1] = 1 / (Ez_a[k] - Ez_alf[k, i])
        self._Ez_alf = numba.cuda.to_device(Ez_alf)
        # * scratchpad arrays for Dirichlet boundary conditions solver
        self._Ez_bet = numba.cuda.device_array((N, N))

        self.dct_plan = pyculib.fft.FFTPlan(shape=(2 * N - 2,),
                                            itype=np.float64,
                                            otype=np.complex128,
                                            batch=(4 * N))
        # (2 * N - 2) // 2 + 1 == (N - 1) + 1 == N
        self._combined_dct1_in = numba.cuda.device_array((4 * N, 2 * N - 2))
        self._combined_dct1_out = numba.cuda.device_array((4 * N, N), dtype=np.complex128)
        self._combined_dct2_in = numba.cuda.device_array((4 * N, 2 * N - 2))
        self._combined_dct2_out = numba.cuda.device_array((4 * N, N), dtype=np.complex128)
        self._Ex_dct1_in = self._combined_dct1_in[:N, :]
        self._Ex_dct1_out = self._combined_dct1_out[:N, :]
        self._Ex_dct2_in = self._combined_dct2_in[:N, :]
        self._Ex_dct2_out = self._combined_dct2_out[:N, :]
        self._Ey_dct1_in = self._combined_dct1_in[N:2*N, :]
        self._Ey_dct1_out = self._combined_dct1_out[N:2*N, :]
        self._Ey_dct2_in = self._combined_dct2_in[N:2*N, :]
        self._Ey_dct2_out = self._combined_dct2_out[N:2*N, :]
        self._Bx_dct1_in = self._combined_dct1_in[2*N:3*N, :]
        self._Bx_dct1_out = self._combined_dct1_out[2*N:3*N, :]
        self._Bx_dct2_in = self._combined_dct2_in[2*N:3*N, :]
        self._Bx_dct2_out = self._combined_dct2_out[2*N:3*N, :]
        self._By_dct1_in = self._combined_dct1_in[3*N:, :]
        self._By_dct1_out = self._combined_dct1_out[3*N:, :]
        self._By_dct2_in = self._combined_dct2_in[3*N:, :]
        self._By_dct2_out = self._combined_dct2_out[3*N:, :]
        self._Ex = numba.cuda.device_array((N, N))
        self._Ey = numba.cuda.device_array((N, N))
        self._Bx = numba.cuda.device_array((N, N))
        self._By = numba.cuda.device_array((N, N))
        self._Ex_sub = numba.cuda.device_array((N, N))
        self._Ey_sub = numba.cuda.device_array((N, N))
        self._Bx_sub = numba.cuda.device_array((N, N))
        self._By_sub = numba.cuda.device_array((N, N))

        self.dst_plan = pyculib.fft.FFTPlan(shape=(2 * N - 2,),
                                            itype=np.float64,
                                            otype=np.complex128,
                                            batch=(N - 2))
        self._Ez_dst1_in = numba.cuda.device_array((N - 2, 2 * N - 2))
        self._Ez_dst1_out = numba.cuda.device_array((N - 2, N), dtype=np.complex128)
        self._Ez_dst2_in = numba.cuda.device_array((N - 2, 2 * N - 2))
        self._Ez_dst2_out = numba.cuda.device_array((N - 2, N), dtype=np.complex128)
        self._Ez = numba.cuda.device_array((N, N))

        self._Ez_dst1_in[:, :] = 0
        self._Ez_dst2_in[:, :] = 0
        self._Ez[:, :] = 0

        # total multiplier to compensate for the iDST+DST transforms
        self.Ez_mul = self.grid_step_size**2
        self.Ez_mul /= 2 * N - 2  # don't ask

        # total multiplier to compensate for the iDCT+DCT transforms
        self.mix_mul = self.grid_step_size**2
        self.mix_mul /= 2 * N - 2  # don't ask

        self._Bz = numba.cuda.device_array((N, N))
        self._Bz[:, :] = 0  # Bz = 0 for now

        self._ro_initial = numba.cuda.device_array((N, N))
        self._ro = numba.cuda.device_array((N, N))
        self._jx = numba.cuda.device_array((N, N))
        self._jy = numba.cuda.device_array((N, N))
        self._jz = numba.cuda.device_array((N, N))

        self._beam_ro = numba.cuda.device_array((N, N))

        self._jx_prev = numba.cuda.device_array((N, N))
        self._jy_prev = numba.cuda.device_array((N, N))

        self._Ex_prev = numba.cuda.device_array((N, N))
        self._Ey_prev = numba.cuda.device_array((N, N))
        self._Ez_prev = numba.cuda.device_array((N, N))
        self._Bx_prev = numba.cuda.device_array((N, N))
        self._By_prev = numba.cuda.device_array((N, N))
        self._Bz_prev = numba.cuda.device_array((N, N))

        self._Ex_avg = numba.cuda.device_array((N, N))
        self._Ey_avg = numba.cuda.device_array((N, N))
        self._Ez_avg = numba.cuda.device_array((N, N))
        self._Bx_avg = numba.cuda.device_array((N, N))
        self._By_avg = numba.cuda.device_array((N, N))
        self._Bz_avg = numba.cuda.device_array((N, N))


    def preload(self, Ex_prev, Ey_prev, Ez_prev, Bx_prev, By_prev, Bz_prev,
                jx_prev, jy_prev):
        self._Ex_prev[:, :] = np.ascontiguousarray(Ex_prev)
        self._Ey_prev[:, :] = np.ascontiguousarray(Ey_prev)
        self._Ez_prev[:, :] = np.ascontiguousarray(Ez_prev)
        self._Bx_prev[:, :] = np.ascontiguousarray(Bx_prev)
        self._By_prev[:, :] = np.ascontiguousarray(By_prev)
        self._Bz_prev[:, :] = np.ascontiguousarray(Bz_prev)
        self._jx_prev[:, :] = np.ascontiguousarray(jx_prev)
        self._jy_prev[:, :] = np.ascontiguousarray(jy_prev)

        self._Ex[:, :] = self._Ex_prev
        self._Ey[:, :] = self._Ey_prev
        self._Ez[:, :] = self._Ez_prev
        self._Bx[:, :] = self._Bx_prev
        self._By[:, :] = self._By_prev
        self._Bz[:, :] = self._Bz_prev

        self._Ex_avg[:, :] = self._Ex
        self._Ey_avg[:, :] = self._Ey
        self._Ez_avg[:, :] = self._Ez
        self._Bx_avg[:, :] = self._Bx
        self._By_avg[:, :] = self._By
        self._Bz_avg[:, :] = self._Bz

    def load(self, plasma, beam_ro, hs_xs, hs_ys, Ex_sub, Ey_sub, Bx_sub, By_sub):
        Nc = self._Nc
        self._m[:, :] = np.ascontiguousarray(plasma['m'].reshape(Nc, Nc))
        self._q[:, :] = np.ascontiguousarray(plasma['q'].reshape(Nc, Nc))
        self._x[:, :] = np.ascontiguousarray(plasma['x'].reshape(Nc, Nc))
        self._y[:, :] = np.ascontiguousarray(plasma['y'].reshape(Nc, Nc))
        self._px[:, :] = np.ascontiguousarray(plasma['p'][:, 1].reshape(Nc, Nc))
        self._py[:, :] = np.ascontiguousarray(plasma['p'][:, 2].reshape(Nc, Nc))
        self._pz[:, :] = np.ascontiguousarray(plasma['p'][:, 0].reshape(Nc, Nc))

        if hs_xs is not 0:
            self._halfstep_x[:, :] = np.ascontiguousarray(hs_xs.reshape(Nc, Nc))
        if hs_ys is not 0:
            self._halfstep_y[:, :] = np.ascontiguousarray(hs_ys.reshape(Nc, Nc))

        roj_init_kernel[self.cfg](self._ro.ravel(), self._jx.ravel(),
                                  self._jy.ravel(), self._jz.ravel(),
                                  self._ro_initial.ravel())
        numba.cuda.synchronize()

        self._beam_ro[:, :] = np.ascontiguousarray(beam_ro)
        self._Ex_sub[:, :] = np.ascontiguousarray(Ex_sub)
        self._Ey_sub[:, :] = np.ascontiguousarray(Ey_sub)
        self._Bx_sub[:, :] = np.ascontiguousarray(Bx_sub)
        self._By_sub[:, :] = np.ascontiguousarray(By_sub)

        self.___plasma = plasma.copy()


    def interpolate(self):
        interpolate_kernel[self.cfg](self._halfstep_x.ravel(),
                                     self._halfstep_y.ravel(),
                                     self._Ex_avg, self._Ey_avg, self._Ez_avg,
                                     self._Bx_avg, self._By_avg, self._Bz_avg,
                                     self.grid_step_size, self.grid_steps,
                                     self._Exs.ravel(), self._Eys.ravel(),
                                     self._Ezs.ravel(), self._Bxs.ravel(),
                                     self._Bys.ravel(), self._Bzs.ravel())
        numba.cuda.synchronize()


    def move_smart(self):
        move_smart_kernel[self.cfg](self.xi_step_size,
                                    self.particle_boundary,
                                    self._m.ravel(), self._q.ravel(),
                                    self._x.ravel(), self._y.ravel(),
                                    self._px.ravel(), self._py.ravel(),
                                    self._pz.ravel(),
                                    self._Exs.ravel(), self._Eys.ravel(),
                                    self._Ezs.ravel(), self._Bxs.ravel(),
                                    self._Bys.ravel(), self._Bzs.ravel(),
                                    self._new_x.ravel(), self._new_y.ravel(),
                                    self._new_px.ravel(), self._new_py.ravel(),
                                    self._new_pz.ravel())
        numba.cuda.synchronize()


    def deposit(self):
        deposit_kernel[self.cfg](self.grid_steps, self.grid_step_size,
                                 self._new_x, self._new_y, self._m, self._q,
                                 self._new_px, self._new_py, self._new_pz,
                                 self._A_weights, self._B_weights,
                                 self._C_weights, self._D_weights,
                                 self._indices_prev, self._indices_next,
                                 self.virtplasma_smallness_factor,
                                 self._ro, self._jx, self._jy, self._jz)
        numba.cuda.synchronize()

    def initial_deposition(self, config, plasma_initial):
        self._ro_initial[:, :] = 0
        self._ro[:, :] = 0
        self._jx[:, :] = 0
        self._jy[:, :] = 0
        self._jz[:, :] = 0

        Nc = self._Nc
        self._m[:, :] = np.ascontiguousarray(plasma_initial['m'].reshape(Nc, Nc))
        self._q[:, :] = np.ascontiguousarray(plasma_initial['q'].reshape(Nc, Nc))
        self._new_x[:, :] = np.ascontiguousarray(plasma_initial['x'].reshape(Nc, Nc))
        self._new_y[:, :] = np.ascontiguousarray(plasma_initial['y'].reshape(Nc, Nc))
        self._new_px[:, :] = np.ascontiguousarray(plasma_initial['p'][:, 1].reshape(Nc, Nc))
        self._new_py[:, :] = np.ascontiguousarray(plasma_initial['p'][:, 2].reshape(Nc, Nc))
        self._new_pz[:, :] = np.ascontiguousarray(plasma_initial['p'][:, 0].reshape(Nc, Nc))

        self.deposit()

        self._ro_initial[:, :] = -np.array(self._ro.copy_to_host())
        numba.cuda.synchronize()


    def calculate_Ex_Ey_Bx_By(self):
        # The grand plan: mul * iDCT(SPECTRAL_MAGIC(DCT(in.T).T)).T).T for Ex/By
        # and mul * iDCT(SPECTRAL_MAGIC(DCT(in).T)).T) for Ey/Bx
        # where iDCT is DCT;
        # and DCT is jury-rigged from symmetrically-padded DFT
        self.calculate_RHS_Ex_Ey_Bx_By()
        self.calculate_Ex_Ey_Bx_By_1()
        self.calculate_Ex_Ey_Bx_By_2()
        self.calculate_Ex_Ey_Bx_By_3()
        self.calculate_Ex_Ey_Bx_By_4()

    def calculate_RHS_Ex_Ey_Bx_By(self):
        calculate_RHS_Ex_Ey_Bx_By_kernel[self.cfg](self._Ex_sub,
                                                   self._Ey_sub,
                                                   self._Bx_sub,
                                                   self._By_sub,
                                                   self._beam_ro,
                                                   self._ro,
                                                   self._jx,
                                                   self._jx_prev,
                                                   self._jy,
                                                   self._jy_prev,
                                                   self._jz,
                                                   self.grid_step_size, self.xi_step_size,
                                                   self.subtraction_trick,
                                                   self._Ex_dct1_in,
                                                   self._Ey_dct1_in,
                                                   self._Bx_dct1_in,
                                                   self._By_dct1_in)
        numba.cuda.synchronize()

    def calculate_Ex_Ey_Bx_By_1(self):
        # 1. Apply iDCT-1 (Discrete Cosine Transform Type 1) to the RHS
        # iDCT-1 is just DCT-1 in cuFFT
        self.dct_plan.forward(self._combined_dct1_in.ravel(),
                              self._combined_dct1_out.ravel())
        numba.cuda.synchronize()
        # This implementation of DCT is real-to-complex, so scrapping the i, j
        # element of the transposed answer would be dct1_out[j, i].real

    def calculate_Ex_Ey_Bx_By_2(self):
        # 2. Solve tridiagonal matrix equation for each spectral column with Thomas method:
        mid_dct_transform[self.cfg](self._Ex_dct1_out, self._Ex_dct2_in,
                                    self._Ey_dct1_out, self._Ey_dct2_in,
                                    self._Bx_dct1_out, self._Bx_dct2_in,
                                    self._By_dct1_out, self._By_dct2_in,
                                    self._Ex_bet, self._Ey_bet,
                                    self._Bx_bet, self._By_bet,
                                    self._mix_alf, self.mix_mul)
        numba.cuda.synchronize()

    def calculate_Ex_Ey_Bx_By_3(self):
        # 3. Apply DCT-1 (Discrete Cosine Transform Type 1) to the transformed spectra
        self.dct_plan.forward(self._combined_dct2_in.ravel(),
                              self._combined_dct2_out.ravel())
        numba.cuda.synchronize()

    def calculate_Ex_Ey_Bx_By_4(self):
        # 4. Transpose the resulting Ex (TODO: fuse this step into later steps?)
        unpack_Ex_Ey_Bx_By_fields_kernel[self.cfg](self._Ex_dct2_out,
                                                   self._Ey_dct2_out,
                                                   self._Bx_dct2_out,
                                                   self._By_dct2_out,
                                                   self._Ex, self._Ey,
                                                   self._Bx, self._By)
        numba.cuda.synchronize()


    def calculate_Ez(self):
        # The grand plan: mul * iDST(SPECTRAL_MAGIC(DST(in).T)).T)
        # where iDST is DST;
        # and DST is jury-rigged from symmetrically-padded DFT
        self.calculate_RHS_Ez()
        self.calculate_Ez_1()
        self.calculate_Ez_2()
        self.calculate_Ez_3()
        self.calculate_Ez_4()

    def calculate_RHS_Ez(self):
        calculate_RHS_Ez_kernel[self.cfg](self._jx, self._jy,
                                          self.grid_step_size,
                                          self._Ez_dst1_in)
        numba.cuda.synchronize()

    def calculate_Ez_1(self):
        # 1. Apply iDST-1 (Discrete Sine Transform Type 1) to the RHS
        # iDST-1 is just DST-1 in cuFFT
        self.dst_plan.forward(self._Ez_dst1_in.ravel(),
                              self._Ez_dst1_out.ravel())
        numba.cuda.synchronize()
        # This implementation of DST is real-to-complex, so scrapping the i, j
        # element of the transposed answer would be -dst1_out[j, i + 1].imag

    def calculate_Ez_2(self):
        # 2. Solve tridiagonal matrix equation for each spectral column with Thomas method:
        mid_dst_transform[self.cfg](self._Ez_dst1_out, self._Ez_dst2_in,
                                    self._Ez_bet, self._Ez_alf, self.Ez_mul)
        numba.cuda.synchronize()

    def calculate_Ez_3(self):
        # 3. Apply DST-1 (Discrete Sine Transform Type 1) to the transformed spectra
        self.dst_plan.forward(self._Ez_dst2_in.ravel(),
                              self._Ez_dst2_out.ravel())
        numba.cuda.synchronize()

    def calculate_Ez_4(self):
        # 4. Transpose the resulting Ex (TODO: fuse this step into later steps?)
        unpack_Ez_kernel[self.cfg](self._Ez_dst2_out, self._Ez,
                                   self._Ez_dst1_in, self._Ez_dst1_out, self._Ez_dst2_in)
        numba.cuda.synchronize()


    def average_fields(self):
        average_arrays_kernel[self.cfg](self._Ex_prev.ravel(), self._Ex.ravel(), self._Ex_avg.ravel())
        average_arrays_kernel[self.cfg](self._Ey_prev.ravel(), self._Ey.ravel(), self._Ey_avg.ravel())
        average_arrays_kernel[self.cfg](self._Ez_prev.ravel(), self._Ez.ravel(), self._Ez_avg.ravel())
        average_arrays_kernel[self.cfg](self._Bx_prev.ravel(), self._Bx.ravel(), self._Bx_avg.ravel())
        average_arrays_kernel[self.cfg](self._By_prev.ravel(), self._By.ravel(), self._By_avg.ravel())
        # average_arrays_kernel[self.cfg](self._Bz_prev.ravel(), self._Bz.ravel(), self._Bz_avg.ravel())  # 0 for now
        numba.cuda.synchronize()


    def step(self, config, plasma_old, beam_ro,
             hs_x, hs_y,
             Ex_sub, Ey_sub, Bx_sub, By_sub):

        self.load(plasma_old, beam_ro, hs_x, hs_y, Ex_sub, Ey_sub, Bx_sub, By_sub)
        self.interpolate()
        self.move_smart()

        self.deposit()
        self.calculate_Ex_Ey_Bx_By()
        self.calculate_Ez()
        self.average_fields()

        return self.unload(config)


    def unload(self, config):
        roj = np.zeros((config.n_dim, config.n_dim), dtype=RoJ_dtype)
        roj['ro'] = self._ro.copy_to_host()
        roj['jx'] = self._jx.copy_to_host()
        roj['jy'] = self._jy.copy_to_host()
        roj['jz'] = self._jz.copy_to_host()

        Ex = self._Ex.copy_to_host()
        Ey = self._Ey.copy_to_host()
        Ez = self._Ez.copy_to_host()
        Bx = self._Bx.copy_to_host()
        By = self._By.copy_to_host()
        Bz = self._Bz.copy_to_host()

        Ex_avg = self._Ex_avg.copy_to_host()
        Ey_avg = self._Ey_avg.copy_to_host()
        Ez_avg = self._Ez_avg.copy_to_host()
        Bx_avg = self._Bx_avg.copy_to_host()
        By_avg = self._By_avg.copy_to_host()
        Bz_avg = self._Bz_avg.copy_to_host()

        plasma = self.___plasma
        plasma['m'] = self._m.reshape(plasma.shape)
        plasma['q'] = self._q.reshape(plasma.shape)
        plasma['x'] = self._new_x.reshape(plasma.shape)
        plasma['y'] = self._new_y.reshape(plasma.shape)
        plasma['p'][:, 1] = self._new_px.reshape(plasma.shape)
        plasma['p'][:, 2] = self._new_py.reshape(plasma.shape)
        plasma['p'][:, 0] = self._new_pz.reshape(plasma.shape)

        numba.cuda.synchronize()

        return roj, plasma, Ex, Ey, Ez, Bx, By, Bz, Ex_avg, Ey_avg, Ez_avg, Bx_avg, By_avg, Bz_avg


# TODO: try local arrays for bet?
