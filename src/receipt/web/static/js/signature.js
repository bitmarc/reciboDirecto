// Captura de firma en canvas. La usan tres pantallas:
//
//   /ajustes            firma del emisor, una sola vez (RN-20)
//   /recibos/{id}       firma del cliente sobre un recibo ya emitido (RN-21)
//   /cobrar             firma del cliente en el momento del cobro (§7.2)
//
// El canvas nunca se rellena de blanco: el PNG sale con fondo transparente para
// poder estamparse sobre el recibo (§3.1).
//
// DOS CONTEXTOS, UNA REGLA. En /ajustes y en /recibos el formulario existe solo
// para firmar, así que enviarlo vacío no tiene sentido y se bloquea. En /cobrar
// la firma es OPCIONAL (RN-21) y el formulario es el del cobro entero: bloquear
// ahí impediría cobrar a quien no pide firma, que es el caso normal. Lo declara
// el propio formulario con `data-firma-obligatoria`, en vez de que este archivo
// adivine en qué pantalla está.

(function () {
  "use strict";

  var canvas = document.getElementById("signature-canvas");
  if (!canvas || typeof SignaturePad === "undefined") {
    return;
  }

  var field = document.getElementById("signature-data");
  // El formulario que contiene el canvas, no uno buscado por identificador: en
  // /cobrar el formulario es el del cobro y no se llama "signature-form".
  var form = canvas.closest("form");
  var obligatoria = form !== null && form.hasAttribute("data-firma-obligatoria");
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

  if (form && field) {
    form.addEventListener("submit", function (event) {
      if (pad.isEmpty()) {
        if (obligatoria) {
          event.preventDefault();
        }
        // Sin firma no se manda campo: el servidor solo la estampa si llega
        // algo, y un recibo sin firma del cliente es perfectamente válido.
        field.value = "";
        return;
      }
      field.value = pad.toDataURL("image/png");
    });
  }

  // El canvas puede nacer oculto —en /cobrar vive tras el interruptor "Pedir
  // firma al cliente"— y entonces mide 0×0. Al revelarse hay que remedirlo, o
  // el trazo no aparece donde se dibuja.
  if (window.ResizeObserver) {
    new ResizeObserver(function () {
      if (canvas.offsetWidth > 0) {
        resize();
      }
    }).observe(canvas);
  }

  window.addEventListener("resize", resize);
  window.addEventListener("orientationchange", resize);
  resize();
})();
