/* ************************************************************************

   osparc - the simcore frontend

   https://osparc.io

   Copyright:
     2018 IT'IS Foundation, https://itis.swiss

   License:
     MIT: https://opensource.org/licenses/MIT

   Authors:
     * Odei Maiz (odeimaiz)

************************************************************************ */

/**
 * Built-in service used for selecting a single file from storage and make it available in the workflow
 *
 *   It consists of a widget containing a FilesTree, Add button and Select button:
 * - FilesTree will be populated with data provided by storage service (simcore.S3 and datcore)
 * - Add button will open a dialogue where the selected file will be upload to S3
 * - Select button puts the file in the output of the FilePicker node so that connected nodes can access it.
 *
 * *Example*
 *
 * Here is a little example of how to use the widget.
 *
 * <pre class='javascript'>
 *   let filePicker = new osparc.file.FilePicker(node);
 *   this.getRoot().add(filePicker);
 * </pre>
 */

qx.Class.define("osparc.file.FilePicker", {
  extend: qx.ui.core.Widget,

  /**
    * @param node {osparc.data.model.Node} Node owning the widget
    */
  construct: function(node) {
    this.base(arguments);

    this.set({
      node
    });
  },

  properties: {
    node: {
      check: "osparc.data.model.Node"
    }
  },

  statics: {
    getOutputLabel: function(outputValue) {
      if ("outFile" in outputValue) {
        const outInfo = outputValue["outFile"];
        if ("label" in outInfo) {
          return outInfo.label;
        }
        if ("path" in outInfo) {
          const splitFilename = outInfo.path.split("/");
          return splitFilename[splitFilename.length-1];
        }
        if ("downloadLink" in outInfo) {
          return osparc.file.FileDownloadLink.getOutputLabel(outputValue);
        }
      }
      return null;
    }
  },

  members: {
    __filesTree: null,
    __filesAdder: null,
    __selectBtn: null,
    __mainButtons: null,

    _createChildControlImpl: function(id) {
      let control;
      switch (id) {
        case "reloadButton":
          control = new qx.ui.form.Button().set({
            label: this.tr("Reload"),
            icon: "@FontAwesome5Solid/sync-alt/16",
            allowGrowX: false
          });
          this._add(control);
          break;
        case "filesTree":
          control = new osparc.file.FilesTree();
          this._add(control, {
            flex: 1
          });
          break;
        case "filesAdd":
          control = new osparc.file.FilesAdd().set({
            node: this.getNode()
          });
          this.__mainButtons.add(control);
          break;
        case "selectButton":
          control = new qx.ui.toolbar.Button(this.tr("Select"));
          this.__mainButtons.add(control);
          break;
        case "downloadLink": {
          const groupBox = new qx.ui.groupbox.GroupBox(this.tr("Or provide a Download Link")).set({
            layout: new qx.ui.layout.VBox(5)
          });
          control = new osparc.file.FileDownloadLink();
          groupBox.add(control);
          // SAn: Remove the following line as soon as the node-ports support the download link
          groupBox.exclude();
          this._add(groupBox);
          break;
        }
      }

      return control || this.base(arguments, id);
    },

    buildLayout: function() {
      this._setLayout(new qx.ui.layout.VBox(5));

      const reloadButton = this._createChildControlImpl("reloadButton");
      reloadButton.addListener("execute", function() {
        this.__filesTree.resetCache();
        this.__initResources();
      }, this);

      const filesTree = this.__filesTree = this._createChildControlImpl("filesTree");
      filesTree.addListener("selectionChanged", this.__selectionChanged, this);
      filesTree.addListener("itemSelected", this.__itemSelectedFromStore, this);
      filesTree.addListener("filesAddedToTree", this.__checkSelectedFileIsListed, this);

      const toolbar = new qx.ui.toolbar.ToolBar();
      const mainButtons = this.__mainButtons = new qx.ui.toolbar.Part();
      toolbar.addSpacer();
      toolbar.add(mainButtons);

      this._add(toolbar);

      const filesAdd = this.__filesAdder = this._createChildControlImpl("filesAdd");
      filesAdd.addListener("fileAdded", e => {
        const fileMetadata = e.getData();
        if ("location" in fileMetadata && "dataset" in fileMetadata && "path" in fileMetadata && "name" in fileMetadata) {
          this.__setOutputFileFromStore(fileMetadata["location"], fileMetadata["dataset"], fileMetadata["path"], fileMetadata["name"]);
        }
        this.__filesTree.resetCache();
        this.__initResources();
      }, this);

      const fileDownloadLink = this.__downloadLink = this._createChildControlImpl("downloadLink");
      fileDownloadLink.addListener("fileLinkAdded", e => {
        const downloadLink = e.getData();
        const label = osparc.file.FileDownloadLink.extractLabelFromLink(downloadLink);
        this.__setOutputFileFromLink(downloadLink, label);
      }, this);

      const selectBtn = this.__selectBtn = this._createChildControlImpl("selectButton");
      selectBtn.setEnabled(false);
      selectBtn.addListener("execute", function() {
        this.__itemSelectedFromStore();
      }, this);
    },

    init: function() {
      if (this.__isOutputFileSelectedFromStore()) {
        const outFile = this.__getOutputFile();
        this.__filesTree.loadFilePath(outFile.value);
      } else {
        this.__initResources();
      }

      if (this.__isOutputFileSelectedFromLink()) {
        const outFile = this.__getOutputFile();
        this.__downloadLink.setValue(outFile.value["downloadLink"]);
      }
    },

    uploadPendingFiles: function(files) {
      if (files.length > 0) {
        if (files.length > 1) {
          osparc.component.message.FlashMessenger.getInstance().logAs(this.tr("Only one file is accepted"), "ERROR");
        }
        this.__filesAdder.retrieveUrlAndUpload(files[0]);
      }
    },

    __initResources: function() {
      this.__filesTree.populateTree();
    },

    __selectionChanged: function() {
      const data = this.__filesTree.getSelectedFile();
      this.__selectBtn.setEnabled(data ? data["isFile"] : false);
    },

    __itemSelectedFromStore: function() {
      const data = this.__filesTree.getSelectedFile();
      if (data && data["isFile"]) {
        const selectedItem = data["selectedItem"];
        this.__setOutputFileFromStore(selectedItem.getLocation(), selectedItem.getDatasetId(), selectedItem.getFileId(), selectedItem.getLabel());
        this.getNode().repopulateOutputPortData();
      }
    },

    __getOutputFile: function() {
      const outputs = this.getNode().getOutputs();
      return outputs["outFile"];
    },

    __setOutputFileFromStore: function(store, dataset, path, label) {
      if (store !== undefined && path) {
        const outputs = this.__getOutputFile();
        outputs["value"] = {
          store,
          dataset,
          path,
          label
        };
        this.getNode().getStatus().setProgress(100);
      }
    },

    __setOutputFileFromLink: function(downloadLink, label) {
      if (downloadLink) {
        const outputs = this.__getOutputFile();
        outputs["value"] = {
          downloadLink,
          label
        };
        this.getNode().getStatus().setProgress(100);
        this.getNode().repopulateOutputPortData();
      }
    },

    __isOutputFileSelectedFromStore: function() {
      const outFile = this.__getOutputFile();
      if (outFile && "value" in outFile && "path" in outFile.value) {
        return true;
      }
      return false;
    },

    __isOutputFileSelectedFromLink: function() {
      const outFile = this.__getOutputFile();
      if (outFile && "value" in outFile && "downloadLink" in outFile.value) {
        return true;
      }
      return false;
    },

    __checkSelectedFileIsListed: function() {
      if (this.__isOutputFileSelectedFromStore()) {
        const outFile = this.__getOutputFile();
        this.__filesTree.setSelectedFile(outFile.value.path);
        this.__filesTree.fireEvent("selectionChanged");
      }
    }
  }
});
