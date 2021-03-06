#!/usr/bin/python

import unittest

from core import Player, StandardGameLogic, GameState
from server import ServerMsgHandler, ServerGameState

class TestTakingHits(unittest.TestCase):
  def setUp(self):
    self.gl = StandardGameLogic()
    self.gameState = GameState()
    self.gameState.setGameTime(120)
    
  def test_simple_hit_while_game_stopped(self):
    player = Player(1, 1)
    initialHealth = player.health
  
    sentTeam = 1
    sentPlayer = 2
    damage = 2
  
    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
  
    self.assertEqual(initialHealth, player.health)

  def test_simple_hit(self):
    self.gameState.startGame()
    player = Player(1, 1)
    initialHealth = player.health
  
    sentTeam = 2
    sentPlayer = 1
    damage = 2
  
    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
  
    self.assertEqual(initialHealth - damage, player.health)

  def test_self_hit(self):
    self.gameState.startGame()
    player = Player(1, 1)
    initialHealth = player.health
  
    sentTeam = 1
    sentPlayer = 1
    damage = 2
  
    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
  
    self.assertEqual(initialHealth, player.health)

  def test_team_hit(self):
    self.gameState.startGame()
    player = Player(1, 1)
    initialHealth = player.health
  
    sentTeam = 1
    sentPlayer = 2
    damage = 2
  
    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
  
    self.assertEqual(initialHealth - damage, player.health)

  def test_shot_until_dead(self):
    self.gameState.startGame()
    player = Player(1, 1)
    initialHealth = player.health
  
    sentTeam = 2
    sentPlayer = 1
    damage = (player.health // 2) + 1 # this will fail if the player only starts with 2 health :-(
  
    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
    self.assertEqual(initialHealth - damage, player.health)

    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
    self.assertEqual(0, player.health)
    #TODO assert death signal
    
    self.gl.hit(self.gameState, player, sentTeam, sentPlayer, damage)
    self.assertEqual(0, player.health)
    #TODO assert NO death signal

class TestEventReordering(unittest.TestCase):
  """Some very tightly integrated tests of the event queueing and re-oredering."""
  def setUp(self):
    gameState = ServerGameState()

    class StubListeningThread():
      def queueMessageToAll(self, msg):
        pass

    listeningThread = StubListeningThread()
    gameState.setListeningThread(listeningThread)

    gameState.setGameTime(120)
    gameState.startGame()

    self.serverMsgHandler = ServerMsgHandler(listeningThread, gameState)

  def test_singleEvent(self):
    self.serverMsgHandler.handleMsg("E(1,1000,Recv(1,1,H2,1,3))")
    player = self.serverMsgHandler.gameState.players[(1,1)]
    self.assertEqual(2, player.health) # This depends on the player starting with 5 health

  def test_twoIndependantEventsCorrectOrder(self):
    self.serverMsgHandler.handleMsg("E(1,2000,Recv(1,1,H2,1,1))")
    player = self.serverMsgHandler.gameState.players[(1,1)]
    self.assertEqual(4, player.health) # This depends on the player starting with 5 health
    self.serverMsgHandler.handleMsg("E(1,2001,Recv(1,1,H2,1,1))")
    self.assertEqual(3, player.health) # This depends on the player starting with 5 health

  def test_twoIndependantEventsCorrectOrder2(self):
    self.serverMsgHandler.handleMsg("E(1,3000,Recv(1,1,T))")
    self.serverMsgHandler.handleMsg("E(1,3001,Recv(1,1,t))")
    self.serverMsgHandler.handleMsg("E(1,3002,Recv(1,1,T))")
    self.serverMsgHandler.handleMsg("E(1,3003,Recv(1,1,t))")
    player = self.serverMsgHandler.gameState.players[(1,1)]
    self.assertEqual(98, player.ammo) # This depends on the player starting with 100 ammo
    self.serverMsgHandler.handleMsg("E(1,3004,Recv(1,1,FA))")
    self.assertEqual(100, player.ammo) # This depends on the player starting with max 100 ammo

  def test_twoIndependantEventsIncorrectOrder(self):
    self.serverMsgHandler.handleMsg("E(1,4001,Recv(1,1,H2,1,1))")
    player = self.serverMsgHandler.gameState.players[(1,1)]
    self.assertEqual(4, player.health) # This depends on the player starting with 5 health
    self.serverMsgHandler.handleMsg("E(1,4000,Recv(1,1,H2,1,1))")
    self.assertEqual(3, player.health) # This depends on the player starting with 5 health

  def test_twoIndependantEventsIncorrectOrder2(self):
    """Even though the trigger is recieved last by the server, it was recieved first by the client so the FA should be the most recent thing processed whenever we check. That means there should always be 100 ammo."""
    #NB. we have to have the first Tt as otherwise, the point immediatesly-after the FA becomes the confidence point as we assume messages from a single client are ordered correctly. This then means it isn't last as we would expect!
    self.serverMsgHandler.handleMsg("E(1,5000,Recv(1,1,T))")
    self.serverMsgHandler.handleMsg("E(1,5001,Recv(1,1,t))")
    self.serverMsgHandler.handleMsg("E(1,5004,Recv(1,1,FA))")
    player = self.serverMsgHandler.gameState.players[(1,1)]
    self.assertEqual(100, player.ammo) # This depends on the player starting with 100 ammo
    self.serverMsgHandler.handleMsg("E(1,5002,Recv(1,1,T))")
    self.serverMsgHandler.handleMsg("E(1,5003,Recv(1,1,t))")
    self.assertEqual(100, player.ammo) # This depends on the player starting with max 100 ammo

if __name__ == '__main__':
  unittest.main()
