/* ************************************************************************

   osparc - the simcore frontend

   https://osparc.io

   Copyright:
     2019 IT'IS Foundation, https://itis.swiss

   License:
     MIT: https://opensource.org/licenses/MIT

   Authors:
     * Odei Maiz (odeimaiz)

************************************************************************ */

/**
  * @asset(timer4Worker.js)
  * @ignore(Worker)
  */

/**
 * Singleton class that does some network connection checks.
 *
 * It has two levels:
 * - Listens to the online/offline event to check whether there is Internet connection or not.
 * - Checks whether the webserver is reachable by doing some HealthCheck calls.
 *
 * *Example*
 *
 * Here is a little example of how to use the class.
 *
 * <pre class='javascript'>
 *   osparc.io.WatchDog.getInstance().startCheck();
 * </pre>
 */

qx.Class.define("osparc.io.WatchDog", {
  extend: qx.core.Object,
  type: "singleton",

  construct: function() {
    this.__clientHeartbeatPinger = new qx.event.Timer(this.heartbeatInterval);
    this.__clientHeartbeatPinger.addListener("interval", () => {
      this.__pingServer();
    }, this);

    if (window.Worker) {
      this.__clientHeartbeatWWPinger = new Worker("resource/osparc/timer4Worker.js");
      this.__clientHeartbeatWWPinger.onmessage = () => {
        this.__pingWWServer();
      };
    } else {
      console.error("Your browser doesn't support web workers.");
    }

    // register for socket.io event to change the default heartbeat interval
    const socket = osparc.wrapper.WebSocket.getInstance();
    const socketIoEventName = "set_heartbeat_emit_interval";
    socket.removeSlot(socketIoEventName);
    socket.on(socketIoEventName, emitIntervalSeconds => {
      const newInterval = parseInt(emitIntervalSeconds) * 1000;
      this.setHeartbeatInterval(newInterval);
    }, this);
  },

  properties: {
    onLine: {
      check: "Boolean",
      init: false,
      nullable: false,
      apply: "_applyOnLine"
    },

    heartbeatInterval: {
      check: "Number",
      init: 2 * 1000, // in milliseconds
      nullable: false,
      apply: "_applyHeartbeatInterval"
    }
  },

  members: {
    __clientHeartbeatPinger: null,
    __lastPing: null,

    __clientHeartbeatWWPinger: null,
    __lastWWPing: null,

    _applyOnLine: function(value) {
      let logo = osparc.component.widget.LogoOnOff.getInstance();
      if (logo) {
        logo.setOnLine(value);
      }
      value ? this.__clientHeartbeatPinger.start() : this.__clientHeartbeatPinger.stop();

      if (value) {
        this.__clientHeartbeatWWPinger.postMessage(["start", this.getHeartbeatInterval()]);
      } else {
        this.__clientHeartbeatWWPinger.postMessage(["stop"]);
      }
    },

    _applyHeartbeatInterval: function(value) {
      this.__clientHeartbeatPinger.setInterval(value);
      this.__clientHeartbeatWWPinger.postMessage(["start", this.getHeartbeatInterval()]);
    },

    __pingServer: function() {
      const socket = osparc.wrapper.WebSocket.getInstance();
      try {
        const now = Date.now();
        if (this.__lastPing) {
          console.log("ping window offset", now-this.__lastPing-this.getHeartbeatInterval());
        }
        this.__lastPing = now;
        socket.emit("client_heartbeat");
      } catch (error) {
        // no need to handle the error, nor does it need to cause further issues
        // it is ok to eat it up
      }
    },

    __pingWWServer: function() {
      // const socket = osparc.wrapper.WebSocket.getInstance();
      try {
        const now = Date.now();
        if (this.__lastWWPing) {
          console.log("ping worker offset", now-this.__lastWWPing-this.getHeartbeatInterval());
        }
        this.__lastWWPing = now;
        // socket.emit("client_heartbeat");
      } catch (error) {
        // no need to handle the error, nor does it need to cause further issues
        // it is ok to eat it up
      }
    }
  }
});
