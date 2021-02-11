/*
 * oSPARC - The SIMCORE frontend - https://osparc.io
 * Copyright: 2019 IT'IS Foundation - https://itis.swiss
 * License: MIT - https://opensource.org/licenses/MIT
 * Authors: Ignacio Pascual (ignapas)
 */

qx.Class.define("osparc.ui.basic.NodeStatusUI", {
  extend: qx.ui.basic.Atom,

  construct: function(node) {
    this.base(arguments, this.tr("Idle"), "@FontAwesome5Solid/clock/12");

    this.__node = node;
    this.__label = this.getChildControl("label");
    this.__icon = this.getChildControl("icon");

    if (node.isFilePicker()) {
      this.__setupFilepicker();
    } else if (node.isComputational()) {
      this.__setupComputational();
    } else {
      this.__setupInteractive();
    }
  },

  properties: {
    appearance: {
      init: "chip",
      refine: true
    }
  },

  members: {
    __node: null,
    __label: null,
    __icon: null,

    __addClass: function(element, className) {
      if (element) {
        const currentClass = element.getAttribute("class");
        if (currentClass && currentClass.includes(className.trim())) {
          return;
        }
        element.setAttribute("class", ((currentClass || "") + " " + className).trim());
      }
    },

    __removeClass: function(element, className) {
      const currentClass = element.getAttribute("class");
      if (currentClass) {
        const regex = new RegExp(className.trim(), "g");
        element.setAttribute("class", currentClass.replace(regex, ""));
      }
    },

    __setupComputational: function() {
      this.__node.getStatus().bind("runningStatus", this.__label, "value", {
        converter: state => {
          if (state) {
            this.show();
            if (state === "STARTED") {
              state = "Running";
            }
            return qx.lang.String.firstUp(state.toLowerCase());
          }
          this.exclude();
          return null;
        }
      });

      this.__node.getStatus().bind("runningStatus", this.__icon, "source", {
        converter: state => {
          switch (state) {
            case "SUCCESS":
              return "@FontAwesome5Solid/check/12";
            case "FAILED":
            case "ABORTED":
              return "@FontAwesome5Solid/exclamation-circle/12";
            case "PENDING":
            case "PUBLISHED":
            case "STARTED":
            case "RETRY":
              return "@FontAwesome5Solid/circle-notch/12";
            case "UNKNOWN":
            case "NOT_STARTED":
            default:
              return "";
          }
        },
        onUpdate: (source, target) => {
          target.show();
          const state = source.getRunningStatus();
          switch (state) {
            case "SUCCESS":
              this.__removeClass(this.__icon.getContentElement(), "rotate");
              target.setTextColor("ready-green");
              return;
            case "FAILED":
            case "ABORTED":
              this.__removeClass(this.__icon.getContentElement(), "rotate");
              target.setTextColor("failed-red");
              return;
            case "PENDING":
            case "PUBLISHED":
            case "STARTED":
            case "RETRY":
              this.__addClass(this.__icon.getContentElement(), "rotate");
              target.resetTextColor();
              return;
            case "UNKNOWN":
            case "NOT_STARTED":
            default:
              target.exclude();
              return;
          }
        }
      });
    },

    __setupInteractive: function() {
      this.__node.getStatus().bind("interactiveStatus", this.__label, "value", {
        converter: status => {
          if (status === "ready") {
            return this.tr("Ready");
          } else if (status === "failed") {
            return this.tr("Error");
          } else if (status === "starting") {
            return this.tr("Starting...");
          } else if (status === "pending") {
            return this.tr("Pending...");
          } else if (status === "pulling") {
            return this.tr("Pulling...");
          } else if (status === "connecting") {
            return this.tr("Connecting...");
          }
          return status;
        }
      });

      this.__node.getStatus().bind("interactiveStatus", this.__icon, "source", {
        converter: status => {
          if (status === "ready") {
            return "@FontAwesome5Solid/check/12";
          } else if (status === "failed") {
            return "@FontAwesome5Solid/exclamation-circle/12";
          } else if (status === "starting") {
            return "@FontAwesome5Solid/circle-notch/12";
          } else if (status === "pending") {
            return "@FontAwesome5Solid/circle-notch/12";
          } else if (status === "pulling") {
            return "@FontAwesome5Solid/circle-notch/12";
          } else if (status === "connecting") {
            return "@FontAwesome5Solid/circle-notch/12";
          }
          return "";
        },
        onUpdate: (source, target) => {
          if (source.getInteractiveStatus() == null) {
            this.__removeClass(this.__icon.getContentElement(), "rotate");
          } else if (source.getInteractiveStatus() === "ready") {
            this.__removeClass(this.__icon.getContentElement(), "rotate");
            target.setTextColor("ready-green");
          } else if (source.getInteractiveStatus() === "failed") {
            this.__removeClass(this.__icon.getContentElement(), "rotate");
            target.setTextColor("failed-red");
          } else {
            this.__addClass(this.__icon.getContentElement(), "rotate");
            target.resetTextColor();
          }
        }
      });
    },

    __setupFilepicker: function() {
      const node = this.__node;
      this.__node.getStatus().bind("progress", this.__icon, "source", {
        converter: progress => {
          if (progress === 100) {
            return "@FontAwesome5Solid/check/12";
          }
          return "@FontAwesome5Solid/file/12";
        },
        onUpdate: (source, target) => {
          if (source.getProgress() === 100) {
            target.setTextColor("ready-green");
          } else {
            target.resetTextColor();
          }
        }
      });

      this.__node.getStatus().bind("progress", this.__label, "value", {
        converter: progress => {
          if (progress === 100) {
            return osparc.file.FilePicker.getOutputLabel(node.getOutputs());
          }
          return this.tr("Select a file");
        }
      });
    }
  }
});
