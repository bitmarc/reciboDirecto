// Captura de la firma del emisor (RN-20).
//
// El canvas nunca se rellena de blanco: el PNG sale con fondo transparente para
// poder estamparse sobre el recibo (§3.1).

(function () {
  "use strict";

  var canvas = document.getElementById("signature-canvas");
  if (!canvas || typeof SignaturePad === "undefined") {
    return;
  }

  var field = document.getElementById("signature-data");
  var form = document.getElementById("signature-form");
  var clearButton = document.getElementById("signature-clear");
  var saveButton = document.getElementById("signature-save");

  var pad = new SignaturePad(canvas, {
    penColor: "#14181d",
    minWidth: 0.8,
    maxWidth: 2.4,
  });

  // Sin escalar por devicePixelRatio, el trazo sale pixelado justo donde más se
  // usa: la pantalla del celular.
  function resize() {
    var ratio = Math.max(window.devicePixelRatio || 1, 1);
    var data = pad.toData();

    canvas.width = canvas.offsetWidth * ratio;
    canvas.height = canvas.offsetHeight * ratio;
    canvas.getContext("2d").scale(ratio, ratio);

    // `clear()` resetea el buffer; restaurar los trazos evita que un giro de
    // pantalla borre lo que ya se había dibujado.
    pad.clear();
    if (data && data.length) {
      pad.fromData(data);
    }
    syncSaveButton();
  }

  function syncSaveButton() {
    if (saveButton) {
      saveButton.disabled = pad.isEmpty();
    }
  }

  pad.addEventListener("endStroke", syncSaveButton);

  if (clearButton) {
    clearButton.addEventListener("click", function () {
      pad.clear();
      syncSaveButton();
    });
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      if (pad.isEmpty()) {
        event.preventDefault();
        return;
      }
      field.value = pad.toDataURL("image/png");
    });
  }

  window.addEventListener("resize", resize);
  window.addEventListener("orientationchange", resize);
  resize();
})();
