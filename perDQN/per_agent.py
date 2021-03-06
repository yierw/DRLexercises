import random
import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim

from per_buffer import PrioritizedBuffer

def soft_update(target, source, tau):
    """ Perform soft update"""
    for target_param, param in zip(target.parameters(), source.parameters()):
        target_param.data.copy_(target_param.data * (1.0 - tau) + param.data * tau)

def huber_loss(x, y):
    z = torch.abs(x-y).squeeze()
    return torch.where(z < 1., 0.5*z**2, z-0.5)

class PERAgent():
    """
    valid for discrete actioin space
    implement prioritized experience replay
    """

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    iter = 0
    t_step = 0

    def __init__(self, func, o_dim, a_dim, lr = 1e-3, lr_step_size = 3, batch_size = 64,
                 gamma = 0.99, tau = 0.001, buffer_size = int(1e6),
                 update_every = 10, seed = 1234, algorithm = "dqn"):
        """
        o_dim/c_dim: observation space dimension/ # of channels when image as input
        a_dim: action space dimension
        """
        self.o_dim = o_dim
        self.a_dim = a_dim
        self.lr = lr
        self.lr_step_size = lr_step_size
        self.batch_size = batch_size
        self.gamma = gamma
        self.tau = tau
        self.buffer_size = buffer_size
        self.update_every = update_every
        self.seed = seed

        if algorithm.lower() in ("dqn"):
            self.algorithm = "dqn"
        elif algorithm.lower() in ("ddqn", "double dqn", "doubledqn"):
            self.algorithm = "ddqn"
        else:
            raise TypeError("cannot recognize algorithm")

        self.buffer = PrioritizedBuffer(self.buffer_size, self.batch_size, self.seed)

        self.online_net = func(self.o_dim , self.a_dim, self.seed).to(self.device)
        self.target_net = func(self.o_dim , self.a_dim, self.seed).to(self.device)
        self.optimizer = optim.Adam(self.online_net.parameters(), lr = self.lr)
        self.scheduler = optim.lr_scheduler.StepLR(self.optimizer, step_size = self.lr_step_size, gamma = 0.1)

    def get_action(self, state, eps = 0.):
        # Epsilon-greedy action selection
        if random.random() > eps:
            state_tensor = torch.from_numpy(state).float().unsqueeze(0).to(self.device)
            # select action according to online network
            self.online_net.eval()
            with torch.no_grad():
                action = self.online_net(state_tensor).argmax(1).item()
            self.online_net.train()
            return action
        else:
            return random.choice(np.arange(self.a_dim))

    def update(self, experiences):

        idxs, IS_weights, states, actions, rewards, next_states, dones = experiences

        IS_weights = torch.FloatTensor(IS_weights).to(self.device)
        states = torch.FloatTensor(states).to(self.device)
        next_states = torch.FloatTensor(next_states).to(self.device)

        actions = torch.LongTensor(actions).view(-1, 1).to(self.device)
        rewards = torch.FloatTensor(rewards).view(-1, 1).to(self.device)
        dones = torch.FloatTensor(dones).view(-1, 1).to(self.device)

        if self.algorithm == "ddqn":
            max_actions = self.online_net(next_states).max(1)[1].view(-1, 1)
            Q_next = self.target_net(next_states).gather(1, max_actions)

        elif self.algorithm == "dqn":
            Q_next = self.target_net(next_states).max(1)[0].view(-1, 1)
        else:
            raise TypeError("cannot recognize algorithm")

        Q_targets = rewards + self.gamma * Q_next * (1. -dones)
        Q_expected = self.online_net(states).gather(1, actions)
        # ---------------------------- update online net ---------------------------- #
        #TD_errors = torch.abs(Q_expected - Q_targets.detach()).squeeze()
        TD_errors = huber_loss(Q_expected, Q_targets.detach())

        loss = torch.mean(TD_errors * IS_weights)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.online_net.parameters(), 10.)
        self.optimizer.step()
        # ---------------------------- update priority ---------------------------- #
        self.buffer.update_priority(idxs, TD_errors.cpu().detach().numpy())
        # ---------------------------- update target net ---------------------------- #
        soft_update(self.target_net, self.online_net, self.tau)

    def step(self, state, action, reward, next_state, done):
        self.buffer.push(state, action, reward, next_state, done)
        if (len(self.buffer) <= self.batch_size):
            pass
        else:
            """
            first update happens when we have enough tuples for a batch;
            then update after push update_every tuples
            """
            if self.t_step % self.update_every == 0:
                experiences = self.buffer.sample()
                self.update(experiences)
                self.iter += 1

            self.t_step += 1
