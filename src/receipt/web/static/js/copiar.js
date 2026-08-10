// Copiar la liga del recibo al portapapeles (§7.2).
//
// El botón dice qué pasó, porque copiar es una acción sin resultado visible: si
// no confirma, el emisor la vuelve a tocar y no sabe si funcionó.
//
// `navigator.clipboard` solo existe en contextos seguros (HTTPS o localhost).
// Cuando no está, el respaldo selecciona el texto para que se pueda copiar a
// mano en lugar de dejar un botón muerto.

(function () {
  "use strict";

  function avisar(boton, texto) {
    var original = boton.textContent;
    boton.textContent = texto;
    window.setTimeout(function () {
      boton.textContent = original;
    }, 2000);
  }

  function respaldo(valor) {
    var campo = document.createElement("textarea");
    campo.value = valor;
    campo.setAttribute("readonly", "");
    campo.style.position = "fixed";
    campo.style.opacity = "0";
    document.body.appendChild(campo);
    campo.select();
    try {
      return document.execCommand("copy");
    } catch (error) {
      return false;
    } finally {
      document.body.removeChild(campo);
    }
  }

  document.addEventListener("click", function (event) {
    var boton = event.target.closest("[data-copiar]");
    if (!boton) {
      return;
    }

    var liga = boton.getAttribute("data-copiar");

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(liga).then(
        function () {
          avisar(boton, "Liga copiada");
        },
        function () {
          avisar(boton, respaldo(liga) ? "Liga copiada" : "Copia la liga de abajo");
        }
      );
      return;
    }

    avisar(boton, respaldo(liga) ? "Liga copiada" : "Copia la liga de abajo");
  });
})();
