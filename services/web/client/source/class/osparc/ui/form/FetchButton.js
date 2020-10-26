/*
 * oSPARC - The SIMCORE frontend - https://osparc.io
 * Copyright: 2020 IT'IS Foundation - https://itis.swiss
 * License: MIT - https://opensource.org/licenses/MIT
 * Authors: Ignacio Pascual (ignapas)
 */

/**
 * A button that serves to fetch or load some data from the server. To indicate that some processing is being done, and
 * that the user has to wait, a rotating special icon is shown meanwhile.
 */
qx.Class.define("osparc.ui.form.FetchButton", {
  extend: qx.ui.form.Button,
  include: osparc.ui.mixin.FetchButton
});
