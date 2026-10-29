/**
 * One-time code input.
 *
 * Turns an empty container into single-digit boxes and keeps them behaving
 * like one field: typing advances, backspace steps back, a pasted code fills
 * every box, and the form submits once all digits are present.
 *
 *   const otp = OtpInput.mount(document.getElementById("verify-code-input"), {
 *     length: 6,
 *     firstInputId: "verify-code",
 *     onComplete: () => form.requestSubmit()
 *   });
 */
(function () {
  "use strict";

  const OtpInput = (function () {
    const NON_DIGIT = /[^0-9]/g;

    function mount(container, options) {
      if (!container) return null;

      const settings = options || {};
      const length = Math.min(8, Math.max(4, Number(settings.length) || 6));
      const form = container.closest("form");
      const boxes = [];

      container.classList.add("otp-input");
      container.replaceChildren();
      container.setAttribute("role", "group");
      container.setAttribute(
        "aria-label",
        settings.label || `Verification code, ${length} digits`
      );

      for (let index = 0; index < length; index += 1) {
        const box = document.createElement("input");

        box.type = "text";
        box.inputMode = "numeric";
        box.autocomplete = index === 0 ? "one-time-code" : "off";
        box.maxLength = 1;
        box.className = "otp-digit";
        box.setAttribute("aria-label", `Digit ${index + 1} of ${length}`);

        if (index === 0 && settings.id) {
          box.id = settings.id;
        }

        if (settings.describedBy) {
          box.setAttribute("aria-describedby", settings.describedBy);
        }

        boxes.push(box);
        container.appendChild(box);
      }

      function value() {
        return boxes.map((box) => box.value).join("");
      }

      function isComplete() {
        return value().length === boxes.length;
      }

      function select(box) {
        box.focus();
        if (box.setSelectionRange) {
          box.setSelectionRange(0, box.value.length);
        }
      }

      function focus() {
        select(boxes[0]);
      }

      function clear() {
        boxes.forEach((box) => {
          box.value = "";
        });

        container.classList.remove("is-invalid");
      }

      function setInvalid(invalid) {
        container.classList.toggle("is-invalid", Boolean(invalid));
      }

      function setDisabled(disabled) {
        boxes.forEach((box) => {
          box.disabled = Boolean(disabled);
        });
      }

      function announceComplete() {
        if (!isComplete()) return;

        container.classList.remove("is-invalid");

        if (typeof settings.onComplete === "function") {
          settings.onComplete(value());
        }
      }

      /* Write digits from a position onwards; used by typing and pasting. */
      function fill(digits, startIndex) {
        let cursor = startIndex;

        for (const digit of digits) {
          if (cursor >= boxes.length) break;

          boxes[cursor].value = digit;
          cursor += 1;
        }

        select(boxes[Math.min(cursor, boxes.length - 1)]);
      }

      boxes.forEach((box, index) => {
        box.addEventListener("input", () => {
          const typed = box.value.replace(NON_DIGIT, "");

          if (!typed) {
            box.value = "";
            return;
          }

          if (typed.length > 1) {
            /* Some browsers ignore maxlength when text is dropped in. */
            fill(typed.slice(0, boxes.length - index).split(""), index);
          } else {
            box.value = typed;

            if (index < boxes.length - 1) {
              select(boxes[index + 1]);
            }
          }

          announceComplete();
        });

        box.addEventListener("keydown", (event) => {
          if (event.key === "Backspace" && !box.value) {
            event.preventDefault();

            if (index > 0) {
              boxes[index - 1].value = "";
              select(boxes[index - 1]);
            }

            return;
          }

          if (event.key === "ArrowLeft" && index > 0) {
            event.preventDefault();
            select(boxes[index - 1]);
            return;
          }

          if (event.key === "ArrowRight" && index < boxes.length - 1) {
            event.preventDefault();
            select(boxes[index + 1]);
            return;
          }

          if (event.key === "Enter" && form) {
            event.preventDefault();

            if (!isComplete()) return;

            if (typeof form.requestSubmit === "function") {
              form.requestSubmit();
            } else {
              form.submit();
            }
          }
        });

        box.addEventListener("paste", (event) => {
          const clipboard = event.clipboardData || window.clipboardData;
          const pasted = clipboard
            ? clipboard.getData("text").replace(NON_DIGIT, "")
            : "";

          event.preventDefault();

          if (!pasted) return;

          fill(pasted.slice(0, boxes.length - index).split(""), index);
          announceComplete();
        });

        box.addEventListener("focus", () => {
          if (box.setSelectionRange) {
            box.setSelectionRange(0, box.value.length);
          }
        });
      });

      return {
        value,
        clear,
        fill,
        focus,
        isComplete,
        setDisabled,
        setInvalid
      };
    }

    return { mount };
  })();

  window.OtpInput = OtpInput;
})();
