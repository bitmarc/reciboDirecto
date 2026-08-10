// Pantalla de cobro: mostrar y ocultar sin recargar (§7.2, AC-19).
//
// JavaScript plano y no Alpine. §3.1 nombra Alpine, pero Alpine 3 evalúa sus
// expresiones con `new Function()`, y eso exige `unsafe-eval` en la CSP —
// justo en la pantalla que maneja los datos de cobro. La compilación CSP de
// Alpine obliga a registrar cada componente en JS de todos modos, así que
// acabaría siendo este mismo archivo más una dependencia.
//
// Todo lo que hay aquí es progresivo: sin JavaScript, la pantalla sigue siendo
// un formulario que se puede llenar y enviar. Lo único que se pierde es el
// colapsado.

(function () {
  "use strict";

  var form = document.getElementById("cobro");
  if (!form) {
    return;
  }

  function show(node, visible) {
    if (node) {
      node.hidden = !visible;
    }
  }

  // ---------------------------------------------------------------------
  // Revelados: `data-revela="#id"` sobre una casilla o un botón.
  // ---------------------------------------------------------------------

  form.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-revela]");
    if (!trigger) {
      return;
    }
    var target = form.querySelector(trigger.getAttribute("data-revela"));
    if (!target) {
      return;
    }
    // Una casilla dice si abre o cierra; un botón solo alterna.
    var open = trigger.type === "checkbox" ? trigger.checked : target.hidden;
    show(target, open);
  });

  // ---------------------------------------------------------------------
  // Bloque 1 — Cliente
  // ---------------------------------------------------------------------

  var buscador = form.querySelector("[data-cliente-buscador]");
  var elegido = form.querySelector("[data-cliente-elegido]");
  var alta = form.querySelector("[data-cliente-alta]");
  var nombreElegido = form.querySelector("[data-cliente-nombre]");
  var clienteId = form.querySelector("[data-cliente-id]");
  var trabajosAbiertos = document.getElementById("trabajos-abiertos");

  function elegirCliente(id, nombre) {
    clienteId.value = id;
    nombreElegido.textContent = nombre;
    show(buscador, false);
    show(alta, false);
    show(elegido, true);

    // Los trabajos abiertos son datos, no un paso: los trae HTMX sin recargar.
    if (window.htmx && trabajosAbiertos) {
      window.htmx.ajax("GET", "/cobrar/trabajos?client_id=" + encodeURIComponent(id), {
        target: "#trabajos-abiertos",
      });
    }
  }

  function volverABuscar() {
    clienteId.value = "";
    show(elegido, false);
    show(alta, false);
    show(buscador, true);
    if (trabajosAbiertos) {
      trabajosAbiertos.innerHTML = "";
    }
    mostrarTrabajoNuevo();
  }

  form.addEventListener("click", function (event) {
    var opcion = event.target.closest("[data-elige-cliente]");
    if (opcion) {
      event.preventDefault();
      elegirCliente(
        opcion.getAttribute("data-elige-cliente"),
        opcion.getAttribute("data-nombre") || ""
      );
      return;
    }

    if (event.target.closest("[data-cliente-cambiar]")) {
      volverABuscar();
      return;
    }

    if (event.target.closest("[data-cliente-nuevo]")) {
      // Dar de alta y elegir del directorio son excluyentes: el formulario
      // manda uno u otro, nunca los dos.
      clienteId.value = "";
      show(alta, true);
      var primerCampo = form.querySelector("#new_client_name");
      if (primerCampo) {
        primerCampo.focus();
      }
    }
  });

  // ---------------------------------------------------------------------
  // Bloque 2 — Trabajo nuevo o continuación (RN-16)
  // ---------------------------------------------------------------------

  var trabajoNuevo = form.querySelector("[data-trabajo-nuevo]");

  function mostrarTrabajoNuevo() {
    show(trabajoNuevo, true);
    var extra = form.querySelector("[data-trabajo-extra]");
    show(extra, false);
  }

  form.addEventListener("change", function (event) {
    if (event.target.name !== "service_id") {
      return;
    }
    var continuando = event.target.value !== "";
    // Al continuar, el concepto y el total ya existen y son inmutables (RN-11).
    show(trabajoNuevo, !continuando);
    show(form.querySelector("[data-trabajo-extra]"), continuando);
  });

  form.addEventListener("click", function (event) {
    if (!event.target.closest("[data-trabajo-aparte]")) {
      return;
    }
    // RN-16: crear un trabajo aparte es la otra ruta, igual de explícita.
    var nuevo = form.querySelector('input[name="service_id"][value=""]');
    if (nuevo) {
      nuevo.checked = true;
      nuevo.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });

  // ---------------------------------------------------------------------
  // Envío
  // ---------------------------------------------------------------------

  var enviar = form.querySelector("[data-enviar]");

  form.addEventListener("submit", function () {
    // Doble toque: el servidor ya lo cubre con la clave de idempotencia
    // (AC-18); esto solo evita el segundo envío cuando la señal es mala y la
    // primera petición aún no ha respondido.
    if (enviar) {
      enviar.disabled = true;
      enviar.textContent = "Generando…";
    }
  });
})();
