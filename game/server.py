#!/usr/bin/python

import argparse
import socket
import sys
import time
import traceback
from threading import Thread, Lock, Timer
from bisect import insort

from core import Player, StandardGameLogic, ClientServer, GameState
from ui import MainWindow
from connection import ClientServerConnection
import proto

from PySide.QtGui import QApplication
from PySide.QtCore import Signal

class ServerMsgHandler():
  """A class to handle messages from clients to the server. There should only be one instance of this class""" 
  def __init__(self, listeningThread, gameState):
    self.listeningThread = listeningThread
    self.logic = StandardGameLogic()
    self.gameState = gameState

    #map from id to timestamp
    self.playerLastSeen = {}
    self.confidencePointGameState = gameState
    self.eventsSinceConfidencePoint = [] # a sorted list of (timestamp, event)
    self.confidencePoint = 0
    self.confidencePointMinimumInterval = 10 # Only update the cache at most once this many seconds.

  #so we don't try to process messages from 2 clients at once.
  eventLock = Lock()
    
  def handleMsg(self, fullLine):
    with self.eventLock:
      if mainWindow: # This should only be None in tests.
        mainWindow.lineReceived(fullLine)

      event = proto.parseEvent(fullLine)

      self.playerLastSeen[event.id] = event.time

      #Check if we need to update the confidence point. If so, we will do so after we have parsed/handled this message.
      newConfidencePoint = min(self.playerLastSeen.values())
      updateConfidencePoint = newConfidencePoint > self.confidencePoint + self.confidencePointMinimumInterval

      #insert this event in the correct order (just because it was recieved last, doesn't mean it happened last!)
      insort(self.eventsSinceConfidencePoint, (event.time, event))

      #loop over all events since confidence point, creating a new best-guess gameState.
      self.gameState = self.confidencePointGameState #TODO clone/copy this
      #print("Processing", self.eventsSinceConfidencePoint)
      for currEvent in self.eventsSinceConfidencePoint:
        self.__handleEvent(currEvent[1], self.gameState)

      if updateConfidencePoint:
        self.confidencePointGameState = self.gameState
        self.eventsSinceConfidencePoint = []
        self.confidencePoint = newConfidencePoint

    return "Ack()\n"

  def __handleEvent(self, event, gameState):
    """handle an event, you must be holding self.eventLock before calling this"""
    alreadyHandled = event.handled
    event.handled = True
    msgStr = event.msgStr
    try:
      (recvTeam, recvPlayer, line) = proto.RECV.parse(msgStr)

      try:
        (sentTeam, sentPlayer, damage) = proto.HIT.parse(line)

	#TODO: add some sanity checks in here. The shooting player shouldn't be dead at this point 
	#(although if they are, it could be because we have incomplete data)

        player = gameState.getOrCreatePlayer(recvTeam, recvPlayer)
        self.logic.hit(gameState, player, sentTeam, sentPlayer, damage)
        gameState.playerUpdated.emit(recvTeam, recvPlayer)
      except proto.MessageParseException:
        pass

      try:
        proto.TRIGGER.parse(line)

        player = gameState.getOrCreatePlayer(recvTeam, recvPlayer)
        if (self.logic.trigger(gameState, player)):
          gameState.playerUpdated.emit(recvTeam, recvPlayer)
      except proto.MessageParseException:
        pass

      try:
        proto.FULL_AMMO.parse(line)

        player = gameState.getOrCreatePlayer(recvTeam, recvPlayer)
        if (self.logic.fullAmmo(gameState, player)):
          gameState.playerUpdated.emit(recvTeam, recvPlayer)
      except proto.MessageParseException:
        pass

    except proto.MessageParseException:
      pass

    #TODO: I need to work out what I do with a Hello in the new world of clients having ids and handleEvent being called more than once per event.
    if not alreadyHandled:
      try:
        (teamID, playerID) = proto.HELLO.parse(msgStr)

        if int(teamID) == -1:
          player = gameState.createNewPlayer()
          self.queueMessage(proto.TEAMPLAYER.create(player.teamID, player.playerID))
        else:
          player = gameState.getOrCreatePlayer(teamID, playerID)
          self.queueMessage("Ack()\n")
        #TODO: we need to preserve the sendQueue when we do this
        self.listeningThread.moveConnection(self, player)
          
        if self.gameState.isGameStarted():
          self.queueMessage(proto.STARTGAME.create(self.gameState.gameTimeRemaining()))
      except proto.MessageParseException:
        pass


class Server(ClientServerConnection):
  """A Class for a connection from a client to the Server. There are many instaces of this class, 1 for each connection"""
  def __init__(self, sock, msgHandler):
    ClientServerConnection.__init__(self)
    self.msgHandler = msgHandler

    self.setSocket(sock)
  
  def handleMsg(self, fullLine):
    self.msgHandler.handleMsg(fullLine)

  def onDisconnect(self):
    #not much we can do until they reconnect
    pass


class ListeningThread(Thread):

  def __init__(self, gameState):
    super(ListeningThread, self).__init__(group=None)
    self.name = "Server Listening Thread"
    self.gameState = gameState
    gameState.setListeningThread(self)

    self.msgHandler = ServerMsgHandler(self, gameState)

    self.connections = {}
    self.unestablishedConnections = set()

    self.serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    #self.serversocket.bind((socket.gethostname(), ClientServer.PORT))
    self.serversocket.bind((ClientServer.SERVER, ClientServer.PORT))
    self.serversocket.settimeout(1)
    self.serversocket.listen(5)
    self.shouldStop = False

  def run(self):
    #start serving
    while True:
      if self.shouldStop:
        return

      try:
        (clientsocket, address) = self.serversocket.accept();
        self.unestablishedConnections.add(Server(clientsocket, self.msgHandler))
      except KeyboardInterrupt:
        break
      except socket.timeout:
        pass

  def moveConnection(self, server, player):
    self.unestablishedConnections.remove(server)
    self.connections[(player.teamID, player.playerID)] = server
    
  def queueMessageToAll(self, msg):
    for key in self.connections:
      self.connections[key].queueMessage(msg)

  def queueMessage(self, teamID, playerID, msg):
    if (teamID, playerID) in self.connections:
      self.connections[(teamID, playerID)].queueMessage(msg)

  def movePlayer(self, srcTeamID, srcPlayerID, dstTeamID, dstPlayerID):
    if (srcTeamID, srcPlayerID) in self.connections:
      self.connections[(dstTeamID, dstPlayerID)] = self.connections[(srcTeamID, srcPlayerID)]
      del self.connections[(srcTeamID, srcPlayerID)]
      self.queueMessage(dstTeamID, dstPlayerID, proto.TEAMPLAYER.create(dstTeamID, dstPlayerID))

  def deletePlayer(self, teamID, playerID):
    self.queueMessage(teamID, playerID, proto.DELETED.create())
    if (teamID, playerID) in self.connections:
      del self.connections[(teamID, playerID)]

  def stop(self):
    self.shouldStop = True
    self.serversocket.close()

GAME_TIME=1200 #20 mins
#GAME_TIME=12

class ServerGameState(GameState):
  def __init__(self):
    GameState.__init__(self)
    self.players = {}
    self.teamCount = 0
    self.largestTeam = 0
    self.stopGameTimer = None
    self.targetTeamCount = 2
    self.setGameTime(GAME_TIME)
  
  def setListeningThread(self, lt):
    self.listeningThread = lt

  def getOrCreatePlayer(self, sentTeamStr, sentPlayerStr):
    sentTeam = int(sentTeamStr)
    sentPlayer = int(sentPlayerStr)

    if not (sentTeam, sentPlayer) in self.players:
      self.players[(sentTeam, sentPlayer)] = Player(sentTeam, sentPlayer)
      if sentTeam > self.teamCount:
        self.teamCount = sentTeam
      if sentPlayer > self.largestTeam:
        self.largestTeam = sentPlayer

      self.playerAdded.emit(sentTeam, sentPlayer)
    return self.players[(sentTeam, sentPlayer)]

  def createNewPlayer(self):
    for playerID in range(1, 33):
      for teamID in range(1, self.targetTeamCount + 1):
        if (teamID, playerID) not in self.players:
          return self.getOrCreatePlayer(teamID, playerID)
    #TODO handle this
    raise RuntimeError("too many players")

  def movePlayer(self, srcTeamID, srcPlayerID, dstTeamID, dstPlayerID):
    if (dstTeamID, dstPlayerID) in self.players:
      raise RuntimeError("Tried to move a player to a non-empty spot")
    if (srcTeamID, srcPlayerID) not in self.players:
      return

    player = self.players[(srcTeamID, srcPlayerID)]
    self.players[(dstTeamID, dstPlayerID)] = player
    player.teamID = dstTeamID
    player.playerID = dstPlayerID
    #TODO: should we reset their stats.
    del self.players[(srcTeamID, srcPlayerID)]

    if dstTeamID > self.teamCount:
      self.teamCount = dstTeamID

    if dstPlayerID > self.largestTeam:
      self.largestTeam = dstPlayerID

    if srcTeamID == self.teamCount:
      #check if this was the only player in this team
      self._recalculateTeamCount()

    if srcPlayerID == self.largestTeam:
      #check if this was the only player in this team
      self._recalculateLargestTeam()

    self.listeningThread.movePlayer(srcTeamID, srcPlayerID, dstTeamID, dstPlayerID)
    #TODO: notify people of the change

  def deletePlayer(self, teamID, playerID):
    if (teamID, playerID) not in self.players:
      return

    del self.players[(teamID, playerID)]

    if teamID == self.teamCount:
      #check if this was the only player in this team
      self._recalculateTeamCount()

    if playerID == self.largestTeam:
      #check if this was the only player in this team
      self._recalculateLargestTeam()

    self.listeningThread.deletePlayer(teamID, playerID)

  def _recalculateTeamCount(self):
    for teamID in range(self.teamCount, 0, -1):
      for playerID in range(self.largestTeam, 0, -1):
        if (teamID, playerID) in self.players:
          #still need this team
          self.teamCount = teamID
          return

  def _recalculateLargestTeam(self):
    for playerID in range(self.largestTeam, 0, -1):
      for teamID in range(self.teamCount, 0, -1):
        if (teamID, playerID) in self.players:
          #one team still has this many players
          self.largestTeam = playerID
          return

  def startGame(self):
    GameState.startGame(self)
    def timerStop():
      if self.gameStartTime + self.gameTime > time.time():
        #the game must have been stopped and restarted as we aren't ready to stop yet. Why were we not cancelled though?
        raise RuntimeError("timer seemingly triggered early")
      self.stopGame()
    self.stopGameTimer = Timer(self.gameTime, timerStop)
    self.stopGameTimer.start()
    self.listeningThread.queueMessageToAll(proto.STARTGAME.create(self.gameTime))

  def stopGame(self):
    GameState.stopGame(self)
    self.listeningThread.queueMessageToAll(proto.STOPGAME.create())
    if self.stopGameTimer:
      self.stopGameTimer.cancel()
    self.stopGameTimer = None

  def resetGame(self):
    #GameState.resetGame(self)
    self.listeningThread.queueMessageToAll(proto.RESETGAME.create())
    for p in gameState.players.values():
      p.reset()
      self.playerUpdated.emit(p.teamID, p.playerID)

  def setTargetTeamCount(self, value):
    self.targetTeamCount = value

  def terminate(self):
    self.stopGame()

  playerAdded = Signal(int, int)
  playerUpdated = Signal(int, int)

mainWindow = None

if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='BraidsTag server.')
  args = parser.parse_args()

  gameState = ServerGameState()

  main = ListeningThread(gameState)
  main.start()

  # Create Qt application
  app = QApplication(sys.argv)
  mainWindow = MainWindow(gameState)
  mainWindow.show()

  # Enter Qt main loop
  retval = app.exec_()

  for i in gameState.players.values():
    print i

  main.stop()
  gameState.terminate()

  #print >> sys.stderr, "\n*** STACKTRACE - START ***\n"
  #code = []
  #for threadId, stack in sys._current_frames().items():
  #    code.append("\n# ThreadID: %s" % threadId)
  #    for filename, lineno, name, line in traceback.extract_stack(stack):
  #        code.append('File: "%s", line %d, in %s' % (filename,
  #                                                    lineno, name))
  #        if line:
  #            code.append("  %s" % (line.strip()))
  #
  #for line in code:
  #    print >> sys.stderr, line
  #print >> sys.stderr, "\n*** STACKTRACE - END ***\n"
  #

  sys.exit(retval)
