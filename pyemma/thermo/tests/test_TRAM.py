# This file is part of PyEMMA.
#
# Copyright (c) 2015, 2014 Computational Molecular Biology Group, Freie Universitaet Berlin (GER)
#
# PyEMMA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import
import unittest
from six.moves import range

import numpy as np
import pyemma.thermo
from pyemma.thermo import EmptyState
import warnings
import msmtools


def tower_sample(distribution):
    cdf = np.cumsum(distribution)
    rnd = np.random.rand() * cdf[-1]
    return np.searchsorted(cdf, rnd)

def generate_trajectory(transition_matrices, bias_energies, K, n_samples, x0):
    """generates a list of single-disc TRAM trajs"""

    traj = np.zeros((n_samples,2+bias_energies.shape[0]))
    x = x0
    traj[0,0] = K
    traj[0,1] = x
    traj[0,2:] = bias_energies[:,x]
    h = 1
    for s in range(n_samples-1):
        x_new = tower_sample(transition_matrices[K,x,:])
        x = x_new
        traj[h,0] = K
        traj[h,1] = x
        traj[h,2:] = bias_energies[:,x]
        h += 1
    return traj

def generate_simple_trajectory(transition_matrix, n_samples, x0):
    """generates a single disctraj, sampled according to transition_matrix"""

    n_states = transition_matrix.shape[0]
    traj = np.zeros(n_samples)
    x = x0
    traj[0] = x
    h = 1
    for s in range(n_samples-1):
        x_new = tower_sample(transition_matrix[x,:])
        assert x_new < n_states
        x = x_new
        traj[h] = x
        h += 1
    return traj

def T_matrix(energy):
    n = energy.shape[0]
    metropolis = energy[np.newaxis, :] - energy[:, np.newaxis]
    metropolis[(metropolis < 0.0)] = 0.0
    selection = np.zeros((n,n))
    selection += np.diag(np.ones(n-1)*0.5,k=1)
    selection += np.diag(np.ones(n-1)*0.5,k=-1)
    selection[0,0] = 0.5
    selection[-1,-1] = 0.5
    metr_hast = selection * np.exp(-metropolis)
    for i in range(metr_hast.shape[0]):
        metr_hast[i, i] = 0.0
        metr_hast[i, i] = 1.0 - metr_hast[i, :].sum()
    return metr_hast


class TestTRAMexceptions(unittest.TestCase):
    def test_warning_empty_ensemble(self):
        # have no samples in ensemble #0
        bias_energies = np.zeros((2,2))
        bias_energies[1,:] = np.array([0.0,0.0])
        T = np.zeros((2,2,2))
        T[1,:,:] = T_matrix(bias_energies[1,:])
        n_samples = 100
        trajs = [generate_trajectory(T,bias_energies,1,n_samples,0)]
        tram = pyemma.thermo.TRAM(lag=1)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            tram.estimate(trajs)
            assert len(w) >= 1
            assert any(issubclass(v.category, EmptyState) for v in w)

    def test_exception_wrong_format(self):
        traj = np.zeros((10,3))
        traj[:,0] = 4
        tram = pyemma.thermo.TRAM(lag=1)
        with self.assertRaises(AssertionError):
            tram.estimate([traj])


class TestTRAMwith5StateDTRAMModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bias_energies = np.zeros((2,5))
        cls.bias_energies[0,:] = np.array([1.0,0.0,10.0,0.0,1.0])
        cls.bias_energies[1,:] = np.array([100.0,0.0,0.0,0.0,100.0])
        cls.T = np.zeros((2,5,5))
        cls.T[0,:,:] = T_matrix(cls.bias_energies[0,:])
        cls.T[1,:,:] = T_matrix(cls.bias_energies[1,:])

        n_samples = 50000
        cls.trajs = []
        cls.bias_energies_sh = cls.bias_energies - cls.bias_energies[0,:]
        cls.trajs.append(generate_trajectory(cls.T,cls.bias_energies_sh,0,n_samples,0))
        cls.trajs.append(generate_trajectory(cls.T,cls.bias_energies_sh,0,n_samples,4))
        cls.trajs.append(generate_trajectory(cls.T,cls.bias_energies_sh,1,n_samples,2))

    def test_5_state_model(self):
        self.run_5_state_model(False)

    def test_5_state_model_direct(self):
        self.run_5_state_model(True)

    def run_5_state_model(self, direct_space):
        tram = pyemma.thermo.TRAM(lag=1, maxerr=1E-12, lll_out=10, direct_space=direct_space, nn=1)
        tram.estimate(self.trajs)

        log_pi_K_i = tram.biased_conf_energies.copy()
        log_pi_K_i[0,:] -= np.min(log_pi_K_i[0,:])
        log_pi_K_i[1,:] -= np.min(log_pi_K_i[1,:])

        assert np.allclose(log_pi_K_i, self.bias_energies, atol=0.1)

        # lower bound on the log-likelihood must be maximal at convergence
        assert np.all(tram.logL_history[-1]+1.E-5>=tram.logL_history[0:-1])

        # simple test: just call the methods
        mu = tram.pointwise_unbiased_free_energies()
        x = [traj[:,1] for traj in self.trajs]
        pyemma.thermo.TRAM(mu, x, x, np.arange(0,4).astype(np.float64))


class TestTRAMasReversibleMSM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        n_states = 50
        traj_length = 10000

        traj = np.zeros(traj_length, dtype=int)
        traj[::2] = np.random.randint(1,n_states,size=(traj_length-1)//2+1)

        c = msmtools.estimation.count_matrix(traj, lag=1)
        while not msmtools.estimation.is_connected(c, directed=True):
            traj = np.zeros(traj_length, dtype=int)
            traj[::2] = np.random.randint(1,n_states,size=(traj_length-1)//2+1)
            c = msmtools.estimation.count_matrix(traj, lag=1)

        state_counts = np.bincount(traj)[:,np.newaxis]
        cls.tram_traj = np.zeros((traj_length,3))
        cls.tram_traj[:,1] = traj

        cls.T_ref = msmtools.estimation.tmatrix(c, reversible=True).toarray()
        
    def test_reversible_msm(self):
        self.reversible_msm(False)

    def test_reversible_msm_direct(self):
        self.reversible_msm(True)

    def reversible_msm(self, direct_space):
        tram = pyemma.thermo.TRAM(lag=1,maxerr=1.E-20, lll_out=10, direct_space=direct_space, nn=None)
        tram.estimate(self.tram_traj)
        assert np.allclose(self.T_ref,  tram.models[0].transition_matrix, atol=1.E-4)

        # Lagrange multipliers should be > 0
        assert np.all(tram.log_lagrangian_mult > -1.E300)
        # lower bound on the log-likelihood must be maximal at convergence
        assert np.all(tram.logL_history[-1]+1.E-5>=tram.logL_history[0:-1])

class TestTRAMwithTRAMmodel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # (1-D FEL) TRAM unit test
        # (1) define mu(k,x) on a fine grid, k=0 is defined as unbiased
        # (2) define coarse grid (of Markov states) on x
        # (3) -> compute pi from it and the conditional mu_i^k
        # (4) -> from pi, generate transtion matrix
        # (5) -> run two-level stochastic process to generate the bias trajectories

        # (1)
        n_therm_states = 4
        n_conf_states = 3
        n_micro_states = 50
        traj_length = 20000

        mu = np.zeros((n_therm_states, n_micro_states))
        for k in range(n_therm_states):
            mu[k, :] = np.random.rand(n_micro_states)*0.8 + 0.2
            if k>0:
               mu[k,:] *= (np.random.rand()*0.8 + 0.2)
        energy = -np.log(mu)
        # (2)
        chi = np.zeros((n_micro_states, n_conf_states)) # (crisp)
        for i in range(n_conf_states):
            chi[n_micro_states*i//n_conf_states:n_micro_states*(i+1)//n_conf_states, i] = 1
        assert np.allclose(chi.sum(axis=1), np.ones(n_micro_states))
        # (3)
        #             k  x  i                 k           x  i
        mu_joint = mu[:, :, np.newaxis] * chi[np.newaxis, :, :]
        assert np.allclose(mu_joint.sum(axis=2), mu)
        z = mu_joint.sum(axis=1)
        pi = z / z.sum(axis=1)[:, np.newaxis]
        assert np.allclose(pi.sum(axis=1), np.ones(n_therm_states))
        mu_conditional = mu_joint / z[:, np.newaxis, :]
        assert np.allclose(mu_conditional.sum(axis=1), np.ones((n_therm_states, n_conf_states)))
        # (4)
        T = np.zeros((n_therm_states, n_conf_states, n_conf_states))
        for k in range(n_therm_states):
            T[k,:,:] = T_matrix(-np.log(pi[k,:]))
            assert np.allclose(T[k,:,:].sum(axis=1), np.ones(n_conf_states))
        # (5)
        tramtrajs = [np.zeros((traj_length, 2+n_therm_states)) for k in range(n_therm_states)]
        xes = np.zeros(n_therm_states*traj_length, dtype=int)
        C = np.zeros((n_therm_states, n_conf_states, n_conf_states), dtype=int)
        for k in range(n_therm_states):
            tramtrajs[k][:,0] = k
            tramtrajs[k][:,1] = generate_simple_trajectory(T[k,:,:], traj_length, 0)
            C[k,:,:] = msmtools.estimation.count_matrix(tramtrajs[k][:,1].astype(int), lag=1).toarray()
            for t,i in enumerate(tramtrajs[k][:,1]):
                x = tower_sample(mu_conditional[k,:,int(i)])
                assert mu_conditional[k,x,int(i)] > 0
                xes[k*traj_length+t] = x
                tramtrajs[k][t,2:] = energy[:,x] - energy[0,x] # define k=0 as "unbiased"

        cls.n_conf_states = n_conf_states
        cls.n_therm_states = n_therm_states
        cls.n_micro_states = n_micro_states
        cls.traj_length = traj_length
        cls.tramtrajs = tramtrajs
        cls.z = z
        cls.T = T
        cls.n_therm_states = n_therm_states
        cls.C = C
        cls.energy = energy
        cls.mu = mu
        cls.xes = xes

    def test_with_TRAM_model_direct_space_multi_disc(self):
        # reformat team-trajs to multi-disc format (trivial case)
        new_tramtrajs = []
        for traj in self.tramtrajs:
            new_traj = np.zeros((self.traj_length, 1+2*self.n_therm_states), dtype=np.float64)
            new_traj[:, 0] = traj[:, 0]
            new_traj[:, 1:1 + self.n_therm_states] = traj[:, 1, np.newaxis]
            new_traj[:, 1 + self.n_therm_states:] = traj[:, 2:]
            new_tramtrajs.append(new_traj)
        self.tramtrajs = new_tramtrajs
        self.with_TRAM_model(False, True)

    def test_with_TRAM_model_log_space_multi_disc(self):
        # reformat team-trajs to multi-disc format (trivial case)
        new_tramtrajs = []
        for traj in self.tramtrajs:
            new_traj = np.zeros((self.traj_length, 1+2*self.n_therm_states), dtype=np.float64)
            new_traj[:, 0] = traj[:, 0]
            new_traj[:, 1:1 + self.n_therm_states] = traj[:, 1, np.newaxis]
            new_traj[:, 1 + self.n_therm_states:] = traj[:, 2:]
            new_tramtrajs.append(new_traj)
        self.tramtrajs = new_tramtrajs
        self.with_TRAM_model(True, True)

    def test_with_TRAM_model_direct_space(self):
        self.with_TRAM_model(True, False)

    def test_with_TRAM_model_log_space(self):
        self.with_TRAM_model(False, False)

    def with_TRAM_model(self, direct_space, multi_disc):
        # run TRAM
        tram = pyemma.thermo.TRAM(lag=1, maxerr=1E-12, lll_out=10, direct_space=direct_space, nn=None)
        tram.estimate(self.tramtrajs, multi_disc=multi_disc)

        # csets must include all states
        for k in range(self.n_therm_states):
            assert len(tram.csets[k]) == self.n_conf_states

        # check exact identities
        # (1) sum_j v_j T_ji + v_i = sum_j c_ij + sum_j c_ji
        for k in range(self.n_therm_states):
            lagrangian_mult = np.exp(tram.log_lagrangian_mult[k,:])
            transition_matrix = tram.models[k].transition_matrix
            assert np.allclose(
                lagrangian_mult.T.dot(transition_matrix) + lagrangian_mult,
                self.C[k,:,:].sum(axis=0) + self.C[k,:,:].sum(axis=1))
        # (2) sum_jk v^k_j T^k_ji = sum_jk c^k_ji
        total = np.zeros(self.n_conf_states)
        for k in range(self.n_therm_states):
            lagrangian_mult = np.exp(tram.log_lagrangian_mult[k,:])
            transition_matrix = tram.models[k].transition_matrix
            total += lagrangian_mult.T.dot(transition_matrix)
        assert np.allclose(total, self.C.sum(axis=0).sum(axis=0))

        # check transition matrices
        for k in range(self.n_therm_states):
            assert np.allclose(tram.models[k].transition_matrix, self.T[k,:,:], atol=0.1)

        # check pi
        z_normed = self.z / self.z[0,:].sum()
        assert np.allclose(tram.biased_conf_energies, -np.log(z_normed), atol=0.1)
        pi = np.exp(-tram.biased_conf_energies[0,:])
        pi /= pi.copy().sum()
        assert np.allclose(tram.stationary_distribution, pi) # self-consistency of TRAM

        # check log-likelihood
        assert np.all(tram.logL_history[-1]+1.E-5>=tram.logL_history[0:-1])

        # check mu
        for k in range(self.n_therm_states):
            # reference
            f0 = -np.log(self.mu[k, :].sum())
            reference_fel = self.energy[k, :] - f0
            # TRAM result
            test_p_u_f_es = np.concatenate(tram.pointwise_unbiased_free_energies(k))
            counts,_ = np.histogram(self.xes, weights=np.exp(-test_p_u_f_es), bins=self.n_micro_states)
            test_fel = -np.log(counts) + np.log(counts.sum())
            assert np.allclose(reference_fel, test_fel, atol=0.1)


if __name__ == "__main__":
    unittest.main()

