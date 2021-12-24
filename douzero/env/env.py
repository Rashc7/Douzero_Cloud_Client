from collections import Counter
import numpy as np
import random
import torch
import BidModel

from douzero.env.game import GameEnv

env_version = "3.2.1"
env_url = "http://od.vcccz.com/hechuan/env.py"
Card2Column = {3: 0, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7,
               11: 8, 12: 9, 13: 10, 14: 11, 17: 12}

NumOnes2Array = {0: np.array([0, 0, 0, 0]),
                 1: np.array([1, 0, 0, 0]),
                 2: np.array([1, 1, 0, 0]),
                 3: np.array([1, 1, 1, 0]),
                 4: np.array([1, 1, 1, 1])}

deck = []
for i in range(3, 15):
    deck.extend([i for _ in range(4)])
deck.extend([17 for _ in range(4)])
deck.extend([20, 30])


class Env:
    """
    Doudizhu multi-agent wrapper
    """

    def __init__(self, objective):
        """
        Objective is wp/adp/logadp. It indicates whether considers
        bomb in reward calculation. Here, we use dummy agents.
        This is because, in the orignial game, the players
        are `in` the game. Here, we want to isolate
        players and environments to have a more gym style
        interface. To achieve this, we use dummy players
        to play. For each move, we tell the corresponding
        dummy player which action to play, then the player
        will perform the actual action in the game engine.
        """
        self.objective = objective

        # Initialize players
        # We use three dummy player for the target position
        self.players = {}
        for position in ['landlord', 'landlord_up', 'landlord_down']:
            self.players[position] = DummyAgent(position)

        # Initialize the internal environment
        self._env = GameEnv(self.players)
        self.total_round = 0
        self.force_bid = 0
        self.infoset = None

    def reset(self, model, device, flags=None):
        """
        Every time reset is called, the environment
        will be re-initialized with a new deck of cards.
        This function is usually called when a game is over.
        """
        self._env.reset()

        # Randomly shuffle the deck
        if model is None:
            _deck = deck.copy()
            np.random.shuffle(_deck)
            card_play_data = {'landlord': _deck[:20],
                              'landlord_up': _deck[20:37],
                              'landlord_down': _deck[37:54],
                              'three_landlord_cards': _deck[17:20],
                              }
            for key in card_play_data:
                card_play_data[key].sort()
            self._env.card_play_init(card_play_data)
            self.infoset = self._game_infoset
            return get_obs(self.infoset)
        else:
            _deck = deck.copy()
            np.random.shuffle(_deck)
            cards_ll = _deck[:17]
            cards_llu = _deck[17:34]
            cards_lld = _deck[34:51]
            three_llc = _deck[51:54]
            score1 = BidModel.predict_env(cards_ll)[0]
            score2 = BidModel.predict_env(cards_llu)[0]
            score3 = BidModel.predict_env(cards_lld)[0]
            if score2 > score1:
                cards_ll, cards_llu = cards_llu, cards_ll
            if score3 > score1:
                cards_ll, cards_lld = cards_ll, cards_lld
            if random.random() > 0.5:
                cards_llu, cards_lld = cards_lld, cards_llu
            cards_ll = cards_ll + three_llc
            card_play_data = {'landlord': cards_ll,
                              'landlord_up': cards_llu,
                              'landlord_down': cards_lld,
                              'three_landlord_cards': three_llc,
                              }
            for key in card_play_data:
                card_play_data[key].sort()
            self._env.card_play_init(card_play_data)
            self.infoset = self._game_infoset
            return get_obs(self.infoset), {
                "bid_obs_buffer": None,
                "multiply_obs_buffer": None
            }, self.infoset

    def step(self, action):
        """
        Step function takes as input the action, which
        is a list of integers, and output the next obervation,
        reward, and a Boolean variable indicating whether the
        current game is finished. It also returns an empty
        dictionary that is reserved to pass useful information.
        """
        assert action in self.infoset.legal_actions
        self.players[self._acting_player_position].set_action(action)
        self._env.step()
        self.infoset = self._game_infoset
        done = False
        reward = 0.0
        if self._game_over:
            done = True
            reward = {
                "play": {
                    "landlord": self._get_reward("landlord"),
                    "landlord_up": self._get_reward("landlord_up"),
                    "landlord_down": self._get_reward("landlord_down")
                },
                "bid": {
                    "landlord": self._get_reward_bidding("landlord")*2,
                    "landlord_up": self._get_reward_bidding("landlord_up"),
                    "landlord_down": self._get_reward_bidding("landlord_down")
                }
            }
            obs = None
        else:
            obs = get_obs(self.infoset)
        return obs, reward, done, {}

    def _get_reward(self, pos):
        """
        This function is called in the end of each
        game. It returns either 1/-1 for win/loss,
        or ADP, i.e., every bomb will double the score.
        """
        winner = self._game_winner
        bomb_num = self._game_bomb_num
        self_bomb_num = self._env.pos_bomb_num[pos]
        if winner == 'landlord':
            if self.objective == 'adp':
                return (1 - self._env.step_count * 0.001) * 1.3 ** bomb_num /8
            elif self.objective == 'logadp':
                return (1.0 - self._env.step_count * 0.0033) * 1.3**self_bomb_num * 2**self._env.multiply_count[pos] / 4
            else:
                return 1.0 - self._env.step_count * 0.0033
        else:
            if self.objective == 'adp':
                return (-1 + self._env.step_count * 0.001) * 1.3 ** bomb_num /8
            elif self.objective == 'logadp':
                return (-1.0 + self._env.step_count * 0.0033) * 1.3**self_bomb_num * 2**self._env.multiply_count[pos] / 4
            else:
                return -1.0 + self._env.step_count * 0.0033

    def _get_reward_bidding(self, pos):
        """
        This function is called in the end of each
        game. It returns either 1/-1 for win/loss,
        or ADP, i.e., every bomb will double the score.
        """
        winner = self._game_winner
        bomb_num = self._game_bomb_num
        if winner == 'landlord':
            return 1.0 * 2**(self._env.bid_count-1) / 8
        else:
            return -1.0 * 2**(self._env.bid_count-1) / 8

    @property
    def _game_infoset(self):
        """
        Here, inforset is defined as all the information
        in the current situation, incuding the hand cards
        of all the players, all the historical moves, etc.
        That is, it contains perferfect infomation. Later,
        we will use functions to extract the observable
        information from the views of the three players.
        """
        return self._env.game_infoset

    @property
    def _game_bomb_num(self):
        """
        The number of bombs played so far. This is used as
        a feature of the neural network and is also used to
        calculate ADP.
        """
        return self._env.get_bomb_num()

    @property
    def _game_winner(self):
        """ A string of landlord/peasants
        """
        return self._env.get_winner()

    @property
    def _acting_player_position(self):
        """
        The player that is active. It can be landlord,
        landlod_down, or landlord_up.
        """
        return self._env.acting_player_position

    @property
    def _game_over(self):
        """ Returns a Boolean
        """
        return self._env.game_over


class DummyAgent(object):
    """
    Dummy agent is designed to easily interact with the
    game engine. The agent will first be told what action
    to perform. Then the environment will call this agent
    to perform the actual action. This can help us to
    isolate environment and agents towards a gym like
    interface.
    """

    def __init__(self, position):
        self.position = position
        self.action = None

    def act(self, infoset):
        """
        Simply return the action that is set previously.
        """
        assert self.action in infoset.legal_actions
        return self.action

    def set_action(self, action):
        """
        The environment uses this function to tell
        the dummy agent what to do.
        """
        self.action = action


def get_obs(infoset, use_general=True):
    """
    This function obtains observations with imperfect information
    from the infoset. It has three branches since we encode
    different features for different positions.

    This function will return dictionary named `obs`. It contains
    several fields. These fields will be used to train the model.
    One can play with those features to improve the performance.

    `position` is a string that can be landlord/landlord_down/landlord_up

    `x_batch` is a batch of features (excluding the hisorical moves).
    It also encodes the action feature

    `z_batch` is a batch of features with hisorical moves only.

    `legal_actions` is the legal moves

    `x_no_action`: the features (exluding the hitorical moves and
    the action features). It does not have the batch dim.

    `z`: same as z_batch but not a batch.
    """
    if use_general:
        if infoset.player_position not in ["landlord", "landlord_up", "landlord_down"]:
            raise ValueError('')
        if infoset.player_position == "landlord":
            return _get_obs_general(infoset, infoset.player_position)
        else:
            return _get_obs_mingpai(infoset, infoset.player_position)
    else:
        if infoset.player_position == 'landlord':
            return _get_obs_landlord(infoset)
        elif infoset.player_position == 'landlord_up':
            return _get_obs_landlord_up(infoset)
        elif infoset.player_position == 'landlord_down':
            return _get_obs_landlord_down(infoset)
        else:
            raise ValueError('')


def _get_one_hot_array(num_left_cards, max_num_cards):
    """
    A utility function to obtain one-hot endoding
    """
    one_hot = np.zeros(max_num_cards)
    if num_left_cards > 0:
        one_hot[num_left_cards - 1] = 1

    return one_hot


def _cards2array(list_cards):
    """
    A utility function that transforms the actions, i.e.,
    A list of integers into card matrix. Here we remove
    the six entries that are always zero and flatten the
    the representations.
    """
    if len(list_cards) == 0:
        return np.zeros(54, dtype=np.int8)

    matrix = np.zeros([4, 13], dtype=np.int8)
    jokers = np.zeros(2, dtype=np.int8)
    counter = Counter(list_cards)
    for card, num_times in counter.items():
        if card < 20:
            matrix[:, Card2Column[card]] = NumOnes2Array[num_times]
        elif card == 20:
            jokers[0] = 1
        elif card == 30:
            jokers[1] = 1
    return np.concatenate((matrix.flatten('F'), jokers))


# def _action_seq_list2array(action_seq_list):
#     """
#     A utility function to encode the historical moves.
#     We encode the historical 15 actions. If there is
#     no 15 actions, we pad the features with 0. Since
#     three moves is a round in DouDizhu, we concatenate
#     the representations for each consecutive three moves.
#     Finally, we obtain a 5x162 matrix, which will be fed
#     into LSTM for encoding.
#     """
#     action_seq_array = np.zeros((len(action_seq_list), 54))
#     for row, list_cards in enumerate(action_seq_list):
#         action_seq_array[row, :] = _cards2array(list_cards)
#     # action_seq_array = action_seq_array.reshape(5, 162)
#     return action_seq_array

def _action_seq_list2array(action_seq_list, new_model=True):
    """
    A utility function to encode the historical moves.
    We encode the historical 15 actions. If there is
    no 15 actions, we pad the features with 0. Since
    three moves is a round in DouDizhu, we concatenate
    the representations for each consecutive three moves.
    Finally, we obtain a 5x162 matrix, which will be fed
    into LSTM for encoding.
    """

    if new_model:
        position_map = {"landlord": 0, "landlord_up": 1, "landlord_down": 2}
        action_seq_array = np.ones((len(action_seq_list), 54)) * -1  # Default Value -1 for not using area
        for row, list_cards in enumerate(action_seq_list):
            if list_cards != []:
                action_seq_array[row, :54] = _cards2array(list_cards[1])
    else:
        action_seq_array = np.zeros((len(action_seq_list), 54))
        for row, list_cards in enumerate(action_seq_list):
            if list_cards != []:
                action_seq_array[row, :] = _cards2array(list_cards[1])
        action_seq_array = action_seq_array.reshape(5, 162)
    return action_seq_array

    # action_seq_array = np.zeros((len(action_seq_list), 54))
    # for row, list_cards in enumerate(action_seq_list):
    #     if list_cards != []:
    #         action_seq_array[row, :] = _cards2array(list_cards[1])
    # return action_seq_array


def _process_action_seq(sequence, length=15, new_model=True):
    """
    A utility function encoding historical moves. We
    encode 15 moves. If there is no 15 moves, we pad
    with zeros.
    """
    sequence = sequence[-length:].copy()
    if new_model:
        sequence = sequence[::-1]
    if len(sequence) < length:
        empty_sequence = [[] for _ in range(length - len(sequence))]
        empty_sequence.extend(sequence)
        sequence = empty_sequence
    return sequence


def _get_one_hot_bomb(bomb_num):
    """
    A utility function to encode the number of bombs
    into one-hot representation.
    """
    one_hot = np.zeros(15)
    one_hot[bomb_num] = 1
    return one_hot


def _get_obs_landlord(infoset):
    """
    Obttain the landlord features. See Table 4 in
    https://arxiv.org/pdf/2106.06135.pdf
    """
    num_legal_actions = len(infoset.legal_actions)
    my_handcards = _cards2array(infoset.player_hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)

    other_handcards = _cards2array(infoset.other_hand_cards)
    other_handcards_batch = np.repeat(other_handcards[np.newaxis, :],
                                      num_legal_actions, axis=0)

    last_action = _cards2array(infoset.last_move)
    last_action_batch = np.repeat(last_action[np.newaxis, :],
                                  num_legal_actions, axis=0)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j, action in enumerate(infoset.legal_actions):
        my_action_batch[j, :] = _cards2array(action)

    landlord_up_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_up'], 17)
    landlord_up_num_cards_left_batch = np.repeat(
        landlord_up_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_down'], 17)
    landlord_down_num_cards_left_batch = np.repeat(
        landlord_down_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_played_cards = _cards2array(
        infoset.played_cards['landlord_up'])
    landlord_up_played_cards_batch = np.repeat(
        landlord_up_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_played_cards = _cards2array(
        infoset.played_cards['landlord_down'])
    landlord_down_played_cards_batch = np.repeat(
        landlord_down_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    bomb_num = _get_one_hot_bomb(
        infoset.bomb_num)
    bomb_num_batch = np.repeat(
        bomb_num[np.newaxis, :],
        num_legal_actions, axis=0)

    x_batch = np.hstack((my_handcards_batch,
                         other_handcards_batch,
                         last_action_batch,
                         landlord_up_played_cards_batch,
                         landlord_down_played_cards_batch,
                         landlord_up_num_cards_left_batch,
                         landlord_down_num_cards_left_batch,
                         bomb_num_batch,
                         my_action_batch))
    x_no_action = np.hstack((my_handcards,
                             other_handcards,
                             last_action,
                             landlord_up_played_cards,
                             landlord_down_played_cards,
                             landlord_up_num_cards_left,
                             landlord_down_num_cards_left,
                             bomb_num))
    z = _action_seq_list2array(_process_action_seq(
        infoset.card_play_action_seq, 15, False), False)
    z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    obs = {
        'position': 'landlord',
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'legal_actions': infoset.legal_actions,
        'x_no_action': x_no_action.astype(np.int8),
        'z': z.astype(np.int8),
    }
    return obs


def _get_obs_landlord_up(infoset):
    """
    Obttain the landlord_up features. See Table 5 in
    https://arxiv.org/pdf/2106.06135.pdf
    """
    num_legal_actions = len(infoset.legal_actions)
    my_handcards = _cards2array(infoset.player_hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)

    other_handcards = _cards2array(infoset.other_hand_cards)
    other_handcards_batch = np.repeat(other_handcards[np.newaxis, :],
                                      num_legal_actions, axis=0)

    last_action = _cards2array(infoset.last_move)
    last_action_batch = np.repeat(last_action[np.newaxis, :],
                                  num_legal_actions, axis=0)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j, action in enumerate(infoset.legal_actions):
        my_action_batch[j, :] = _cards2array(action)

    last_landlord_action = _cards2array(
        infoset.last_move_dict['landlord'])
    last_landlord_action_batch = np.repeat(
        last_landlord_action[np.newaxis, :],
        num_legal_actions, axis=0)
    landlord_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord'], 20)
    landlord_num_cards_left_batch = np.repeat(
        landlord_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_played_cards = _cards2array(
        infoset.played_cards['landlord'])
    landlord_played_cards_batch = np.repeat(
        landlord_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    last_teammate_action = _cards2array(
        infoset.last_move_dict['landlord_down'])
    last_teammate_action_batch = np.repeat(
        last_teammate_action[np.newaxis, :],
        num_legal_actions, axis=0)
    teammate_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_down'], 17)
    teammate_num_cards_left_batch = np.repeat(
        teammate_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    teammate_played_cards = _cards2array(
        infoset.played_cards['landlord_down'])
    teammate_played_cards_batch = np.repeat(
        teammate_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    bomb_num = _get_one_hot_bomb(
        infoset.bomb_num)
    bomb_num_batch = np.repeat(
        bomb_num[np.newaxis, :],
        num_legal_actions, axis=0)

    x_batch = np.hstack((my_handcards_batch,
                         other_handcards_batch,
                         landlord_played_cards_batch,
                         teammate_played_cards_batch,
                         last_action_batch,
                         last_landlord_action_batch,
                         last_teammate_action_batch,
                         landlord_num_cards_left_batch,
                         teammate_num_cards_left_batch,
                         bomb_num_batch,
                         my_action_batch))
    x_no_action = np.hstack((my_handcards,
                             other_handcards,
                             landlord_played_cards,
                             teammate_played_cards,
                             last_action,
                             last_landlord_action,
                             last_teammate_action,
                             landlord_num_cards_left,
                             teammate_num_cards_left,
                             bomb_num))
    z = _action_seq_list2array(_process_action_seq(
        infoset.card_play_action_seq, 15, False), False)
    z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    obs = {
        'position': 'landlord_up',
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'legal_actions': infoset.legal_actions,
        'x_no_action': x_no_action.astype(np.int8),
        'z': z.astype(np.int8),
    }
    return obs


def _get_obs_landlord_down(infoset):
    """
    Obttain the landlord_down features. See Table 5 in
    https://arxiv.org/pdf/2106.06135.pdf
    """
    num_legal_actions = len(infoset.legal_actions)
    my_handcards = _cards2array(infoset.player_hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)

    other_handcards = _cards2array(infoset.other_hand_cards)
    other_handcards_batch = np.repeat(other_handcards[np.newaxis, :],
                                      num_legal_actions, axis=0)

    last_action = _cards2array(infoset.last_move)
    last_action_batch = np.repeat(last_action[np.newaxis, :],
                                  num_legal_actions, axis=0)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j, action in enumerate(infoset.legal_actions):
        my_action_batch[j, :] = _cards2array(action)

    last_landlord_action = _cards2array(
        infoset.last_move_dict['landlord'])
    last_landlord_action_batch = np.repeat(
        last_landlord_action[np.newaxis, :],
        num_legal_actions, axis=0)
    landlord_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord'], 20)
    landlord_num_cards_left_batch = np.repeat(
        landlord_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_played_cards = _cards2array(
        infoset.played_cards['landlord'])
    landlord_played_cards_batch = np.repeat(
        landlord_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    last_teammate_action = _cards2array(
        infoset.last_move_dict['landlord_up'])
    last_teammate_action_batch = np.repeat(
        last_teammate_action[np.newaxis, :],
        num_legal_actions, axis=0)
    teammate_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_up'], 17)
    teammate_num_cards_left_batch = np.repeat(
        teammate_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    teammate_played_cards = _cards2array(
        infoset.played_cards['landlord_up'])
    teammate_played_cards_batch = np.repeat(
        teammate_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_played_cards = _cards2array(
        infoset.played_cards['landlord'])
    landlord_played_cards_batch = np.repeat(
        landlord_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    bomb_num = _get_one_hot_bomb(
        infoset.bomb_num)
    bomb_num_batch = np.repeat(
        bomb_num[np.newaxis, :],
        num_legal_actions, axis=0)

    x_batch = np.hstack((my_handcards_batch,
                         other_handcards_batch,
                         landlord_played_cards_batch,
                         teammate_played_cards_batch,
                         last_action_batch,
                         last_landlord_action_batch,
                         last_teammate_action_batch,
                         landlord_num_cards_left_batch,
                         teammate_num_cards_left_batch,
                         bomb_num_batch,
                         my_action_batch))
    x_no_action = np.hstack((my_handcards,
                             other_handcards,
                             landlord_played_cards,
                             teammate_played_cards,
                             last_action,
                             last_landlord_action,
                             last_teammate_action,
                             landlord_num_cards_left,
                             teammate_num_cards_left,
                             bomb_num))
    z = _action_seq_list2array(_process_action_seq(
        infoset.card_play_action_seq, 15, False), False)
    z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    obs = {
        'position': 'landlord_down',
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'legal_actions': infoset.legal_actions,
        'x_no_action': x_no_action.astype(np.int8),
        'z': z.astype(np.int8),
    }
    return obs


def _get_obs_general(infoset, position):
    num_legal_actions = len(infoset.legal_actions)
    my_handcards = _cards2array(infoset.player_hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)

    other_handcards = _cards2array(infoset.other_hand_cards)
    other_handcards_batch = np.repeat(other_handcards[np.newaxis, :],
                                      num_legal_actions, axis=0)

    position_map = {
        "landlord": [1, 0, 0],
        "landlord_up": [0, 1, 0],
        "landlord_down": [0, 0, 1]
    }
    position_info = np.array(position_map[position])
    position_info_batch = np.repeat(position_info[np.newaxis, :],
                                    num_legal_actions, axis=0)

    bid_info = np.array(infoset.bid_info).flatten()
    bid_info_batch = np.repeat(bid_info[np.newaxis, :],
                               num_legal_actions, axis=0)

    multiply_info = np.array(infoset.multiply_info)
    multiply_info_batch = np.repeat(multiply_info[np.newaxis, :],
                                    num_legal_actions, axis=0)

    three_landlord_cards = _cards2array(infoset.three_landlord_cards)
    three_landlord_cards_batch = np.repeat(three_landlord_cards[np.newaxis, :],
                                           num_legal_actions, axis=0)

    last_action = _cards2array(infoset.last_move)
    last_action_batch = np.repeat(last_action[np.newaxis, :],
                                  num_legal_actions, axis=0)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j, action in enumerate(infoset.legal_actions):
        my_action_batch[j, :] = _cards2array(action)

    landlord_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord'], 20)
    landlord_num_cards_left_batch = np.repeat(
        landlord_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_up'], 17)
    landlord_up_num_cards_left_batch = np.repeat(
        landlord_up_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_down'], 17)
    landlord_down_num_cards_left_batch = np.repeat(
        landlord_down_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    other_handcards_left_list = []
    for pos in ["landlord", "landlord_up", "landlord_up"]:
        if pos != position:
            other_handcards_left_list.extend(infoset.all_handcards[pos])

    landlord_played_cards = _cards2array(
        infoset.played_cards['landlord'])
    landlord_played_cards_batch = np.repeat(
        landlord_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_played_cards = _cards2array(
        infoset.played_cards['landlord_up'])
    landlord_up_played_cards_batch = np.repeat(
        landlord_up_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_played_cards = _cards2array(
        infoset.played_cards['landlord_down'])
    landlord_down_played_cards_batch = np.repeat(
        landlord_down_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    bomb_num = _get_one_hot_bomb(
        infoset.bomb_num)
    bomb_num_batch = np.repeat(
        bomb_num[np.newaxis, :],
        num_legal_actions, axis=0)
    num_cards_left = np.hstack((
                         landlord_num_cards_left,  # 20
                         landlord_up_num_cards_left,  # 17
                         landlord_down_num_cards_left))

    x_batch = np.hstack((
                         bid_info_batch,  # 12
                         multiply_info_batch))  # 3
    x_no_action = np.hstack((
                             bid_info,
                             multiply_info))
    z =np.vstack((
                  num_cards_left,
                  my_handcards,  # 54
                  other_handcards,  # 54
                  three_landlord_cards,  # 54
                  landlord_played_cards,  # 54
                  landlord_up_played_cards,  # 54
                  landlord_down_played_cards,  # 54
                  _action_seq_list2array(_process_action_seq(infoset.card_play_action_seq, 32))
                  ))

    _z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    my_action_batch = my_action_batch[:,np.newaxis,:]
    z_batch = np.zeros([len(_z_batch),40,54],int)
    for i in range(0,len(_z_batch)):
        z_batch[i] = np.vstack((my_action_batch[i],_z_batch[i]))
    obs = {
        'position': position,
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'legal_actions': infoset.legal_actions,
        'x_no_action': x_no_action.astype(np.int8),
        'z': z.astype(np.int8),
    }
    return obs


def _get_obs_mingpai(infoset, position):
    num_legal_actions = len(infoset.legal_actions)
    my_handcards = _cards2array(infoset.player_hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)

    upper_handcards = _cards2array(infoset.upper_hand_cards)

    lower_handcards = _cards2array(infoset.lower_hand_cards)

    # three_landlord_cards = _cards2array(infoset.three_landlord_cards)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j, action in enumerate(infoset.legal_actions):
        my_action_batch[j, :] = _cards2array(action)

    landlord_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord'], 20)

    landlord_up_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_up'], 17)

    landlord_down_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict['landlord_down'], 17)

    other_handcards_left_list = []
    for pos in ["landlord", "landlord_up", "landlord_up"]:
        if pos != position:
            other_handcards_left_list.extend(infoset.all_handcards[pos])

    # landlord_played_cards = _cards2array(
    #     infoset.played_cards['landlord'])
    # landlord_played_cards_batch = np.repeat(
    #     landlord_played_cards[np.newaxis, :],
    #     num_legal_actions, axis=0)
    #
    # landlord_up_played_cards = _cards2array(
    #     infoset.played_cards['landlord_up'])
    # landlord_up_played_cards_batch = np.repeat(
    #     landlord_up_played_cards[np.newaxis, :],
    #     num_legal_actions, axis=0)
    #
    # landlord_down_played_cards = _cards2array(
    #     infoset.played_cards['landlord_down'])
    # landlord_down_played_cards_batch = np.repeat(
    #     landlord_down_played_cards[np.newaxis, :],
    #     num_legal_actions, axis=0)

    num_cards_left = np.hstack((
                         landlord_num_cards_left,  # 20
                         landlord_up_num_cards_left,  # 17
                         landlord_down_num_cards_left))

    x_batch = np.array([0])

    z =np.vstack((
                  num_cards_left,
                  my_handcards,  # 54
                  upper_handcards,  # 54
                  lower_handcards,  # 54
                  # three_landlord_cards,  # 54
                  # landlord_played_cards,  # 54
                  # landlord_up_played_cards,  # 54
                  # landlord_down_played_cards,  # 54
                  _action_seq_list2array(_process_action_seq(infoset.card_play_action_seq, 32))
                  ))

    _z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    my_action_batch = my_action_batch[:,np.newaxis,:]
    z_batch = np.zeros([len(_z_batch),37,54],int)
    for i in range(0,len(_z_batch)):
        z_batch[i] = np.vstack((my_action_batch[i],_z_batch[i]))
    obs = {
        'position': position,
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'x_no_action': np.array([0.1]),
        'legal_actions': infoset.legal_actions,
        'z': z.astype(np.int8),
    }
    return obs


def gen_bid_legal_actions(player_id, bid_info):
    self_bid_info = bid_info[:, [(player_id - 1) % 3, player_id, (player_id + 1) % 3]]
    curr_round = -1
    for r in range(4):
        if -1 in self_bid_info[r]:
            curr_round = r
            break
    bid_actions = []
    if curr_round != -1:
        self_bid_info[curr_round] = [0, 0, 0]
        bid_actions.append(np.array(self_bid_info).flatten())
        self_bid_info[curr_round] = [0, 1, 0]
        bid_actions.append(np.array(self_bid_info).flatten())
    return np.array(bid_actions)


def _get_obs_for_bid_legacy(player_id, bid_info, hand_cards):
    all_cards = [3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7, 7,
                 8, 8, 8, 8, 9, 9, 9, 9, 10, 10, 10, 10, 11, 11, 11, 11, 12,
                 12, 12, 12, 13, 13, 13, 13, 14, 14, 14, 14, 17, 17, 17, 17, 20, 30]
    num_legal_actions = 2
    my_handcards = _cards2array(hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)
    other_cards = []
    other_cards.extend(all_cards)
    for card in hand_cards:
        other_cards.remove(card)
    other_handcards = _cards2array(other_cards)
    other_handcards_batch = np.repeat(other_handcards[np.newaxis, :],
                                      num_legal_actions, axis=0)

    position_info = np.array([0, 0, 0])
    position_info_batch = np.repeat(position_info[np.newaxis, :],
                                    num_legal_actions, axis=0)

    bid_legal_actions = gen_bid_legal_actions(player_id, bid_info)
    bid_info = bid_legal_actions[0]
    bid_info_batch = bid_legal_actions

    multiply_info = np.array([0, 0, 0])
    multiply_info_batch = np.repeat(multiply_info[np.newaxis, :],
                                    num_legal_actions, axis=0)

    three_landlord_cards = _cards2array([])
    three_landlord_cards_batch = np.repeat(three_landlord_cards[np.newaxis, :],
                                           num_legal_actions, axis=0)

    last_action = _cards2array([])
    last_action_batch = np.repeat(last_action[np.newaxis, :],
                                  num_legal_actions, axis=0)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j in range(2):
        my_action_batch[j, :] = _cards2array([])

    landlord_num_cards_left = _get_one_hot_array(0, 20)
    landlord_num_cards_left_batch = np.repeat(
        landlord_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_num_cards_left = _get_one_hot_array(0, 17)
    landlord_up_num_cards_left_batch = np.repeat(
        landlord_up_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_num_cards_left = _get_one_hot_array(0, 17)
    landlord_down_num_cards_left_batch = np.repeat(
        landlord_down_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_played_cards = _cards2array([])
    landlord_played_cards_batch = np.repeat(
        landlord_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_played_cards = _cards2array([])
    landlord_up_played_cards_batch = np.repeat(
        landlord_up_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_played_cards = _cards2array([])
    landlord_down_played_cards_batch = np.repeat(
        landlord_down_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    bomb_num = _get_one_hot_bomb(0)
    bomb_num_batch = np.repeat(
        bomb_num[np.newaxis, :],
        num_legal_actions, axis=0)

    x_batch = np.hstack((position_info_batch,
                         my_handcards_batch,
                         other_handcards_batch,
                         three_landlord_cards_batch,
                         last_action_batch,
                         landlord_played_cards_batch,
                         landlord_up_played_cards_batch,
                         landlord_down_played_cards_batch,
                         landlord_num_cards_left_batch,
                         landlord_up_num_cards_left_batch,
                         landlord_down_num_cards_left_batch,
                         bomb_num_batch,
                         bid_info_batch,
                         multiply_info_batch,
                         my_action_batch))
    x_no_action = np.hstack((position_info,
                             my_handcards,
                             other_handcards,
                             three_landlord_cards,
                             last_action,
                             landlord_played_cards,
                             landlord_up_played_cards,
                             landlord_down_played_cards,
                             landlord_num_cards_left,
                             landlord_up_num_cards_left,
                             landlord_down_num_cards_left,
                             bomb_num))
    z = _action_seq_list2array(_process_action_seq([], 32))
    z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    obs = {
        'position': "",
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'legal_actions': bid_legal_actions,
        'x_no_action': x_no_action.astype(np.int8),
        'z': z.astype(np.int8),
        "bid_info_batch": bid_info_batch.astype(np.int8),
        "multiply_info": multiply_info.astype(np.int8)
    }
    return obs


def _get_obs_for_bid(player_id, bid_info, hand_cards):
    all_cards = [3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7, 7,
                 8, 8, 8, 8, 9, 9, 9, 9, 10, 10, 10, 10, 11, 11, 11, 11, 12,
                 12, 12, 12, 13, 13, 13, 13, 14, 14, 14, 14, 17, 17, 17, 17, 20, 30]
    num_legal_actions = 2
    my_handcards = _cards2array(hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)

    bid_legal_actions = gen_bid_legal_actions(player_id, bid_info)
    bid_info = bid_legal_actions[0]
    bid_info_batch = np.hstack([bid_legal_actions for _ in range(5)])

    x_batch = np.hstack((my_handcards_batch,
                         bid_info_batch))
    x_no_action = np.hstack((my_handcards))
    obs = {
        'position': "",
        'x_batch': x_batch.astype(np.float32),
        'z_batch': np.array([0,0]),
        'legal_actions': bid_legal_actions,
        'x_no_action': x_no_action.astype(np.int8),
        "bid_info_batch": bid_info_batch.astype(np.int8)
    }
    return obs


def _get_obs_for_multiply(position, bid_info, hand_cards, landlord_cards):
    all_cards = [3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7, 7,
                 8, 8, 8, 8, 9, 9, 9, 9, 10, 10, 10, 10, 11, 11, 11, 11, 12,
                 12, 12, 12, 13, 13, 13, 13, 14, 14, 14, 14, 17, 17, 17, 17, 20, 30]
    num_legal_actions = 3
    my_handcards = _cards2array(hand_cards)
    my_handcards_batch = np.repeat(my_handcards[np.newaxis, :],
                                   num_legal_actions, axis=0)
    other_cards = []
    other_cards.extend(all_cards)
    for card in hand_cards:
        other_cards.remove(card)
    other_handcards = _cards2array(other_cards)
    other_handcards_batch = np.repeat(other_handcards[np.newaxis, :],
                                      num_legal_actions, axis=0)

    position_map = {
        "landlord": [1, 0, 0],
        "landlord_up": [0, 1, 0],
        "landlord_down": [0, 0, 1]
    }
    position_info = np.array(position_map[position])
    position_info_batch = np.repeat(position_info[np.newaxis, :],
                                    num_legal_actions, axis=0)

    bid_info = np.array(bid_info).flatten()
    bid_info_batch = np.repeat(bid_info[np.newaxis, :],
                               num_legal_actions, axis=0)

    multiply_info = np.array([0, 0, 0])
    multiply_info_batch = np.array([[1, 0, 0],
                                    [0, 1, 0],
                                    [0, 0, 1]])

    three_landlord_cards = _cards2array(landlord_cards)
    three_landlord_cards_batch = np.repeat(three_landlord_cards[np.newaxis, :],
                                           num_legal_actions, axis=0)

    last_action = _cards2array([])
    last_action_batch = np.repeat(last_action[np.newaxis, :],
                                  num_legal_actions, axis=0)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for j in range(num_legal_actions):
        my_action_batch[j, :] = _cards2array([])

    landlord_num_cards_left = _get_one_hot_array(0, 20)
    landlord_num_cards_left_batch = np.repeat(
        landlord_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_num_cards_left = _get_one_hot_array(0, 17)
    landlord_up_num_cards_left_batch = np.repeat(
        landlord_up_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_num_cards_left = _get_one_hot_array(0, 17)
    landlord_down_num_cards_left_batch = np.repeat(
        landlord_down_num_cards_left[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_played_cards = _cards2array([])
    landlord_played_cards_batch = np.repeat(
        landlord_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_up_played_cards = _cards2array([])
    landlord_up_played_cards_batch = np.repeat(
        landlord_up_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    landlord_down_played_cards = _cards2array([])
    landlord_down_played_cards_batch = np.repeat(
        landlord_down_played_cards[np.newaxis, :],
        num_legal_actions, axis=0)

    bomb_num = _get_one_hot_bomb(0)
    bomb_num_batch = np.repeat(
        bomb_num[np.newaxis, :],
        num_legal_actions, axis=0)

    x_batch = np.hstack((position_info_batch,
                         my_handcards_batch,
                         other_handcards_batch,
                         three_landlord_cards_batch,
                         last_action_batch,
                         landlord_played_cards_batch,
                         landlord_up_played_cards_batch,
                         landlord_down_played_cards_batch,
                         landlord_num_cards_left_batch,
                         landlord_up_num_cards_left_batch,
                         landlord_down_num_cards_left_batch,
                         bomb_num_batch,
                         bid_info_batch,
                         multiply_info_batch,
                         my_action_batch))
    x_no_action = np.hstack((position_info,
                             my_handcards,
                             other_handcards,
                             three_landlord_cards,
                             last_action,
                             landlord_played_cards,
                             landlord_up_played_cards,
                             landlord_down_played_cards,
                             landlord_num_cards_left,
                             landlord_up_num_cards_left,
                             landlord_down_num_cards_left,
                             bomb_num))
    z = _action_seq_list2array(_process_action_seq([], 32))
    z_batch = np.repeat(
        z[np.newaxis, :, :],
        num_legal_actions, axis=0)
    obs = {
        'position': "",
        'x_batch': x_batch.astype(np.float32),
        'z_batch': z_batch.astype(np.float32),
        'legal_actions': multiply_info_batch,
        'x_no_action': x_no_action.astype(np.int8),
        'z': z.astype(np.int8),
        "bid_info": bid_info.astype(np.int8),
        "multiply_info_batch": multiply_info.astype(np.int8)
    }
    return obs
