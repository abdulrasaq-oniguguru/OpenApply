// Reads the application form out of the current page. Runs inside the page, so everything is
// capped here before anything is transferred to Python. Pure observation, except that it tags
// the elements it reports with data-oa-id so they can be filled later.
(opts) => {
  const MAX_FIELDS = opts.maxFields;
  const MAX_TEXT = opts.maxText;
  const MAX_OPTIONS = opts.maxOptions;

  const clean = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);

  // Text of an element without the text of controls nested inside it (an implicit
  // <label>Country <select>...</select></label> must not contribute its options).
  const textOf = (el) => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll("select, option, textarea, input, button, script, style").forEach((n) => n.remove());
    return clean(copy.textContent);
  };

  const shown = (el) => {
    if (!el) return false;
    if (typeof el.checkVisibility === "function") {
      if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    } else {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) === 0) return false;
    }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    // pushed off-screen (left:-9999px style traps)
    if (r.right + window.scrollX < 0 || r.bottom + window.scrollY < 0) return false;
    return true;
  };

  const labelsOf = (el) => Array.from(el.labels || []);
  const labelShown = (el) => labelsOf(el).some(shown) || (el.closest("label") ? shown(el.closest("label")) : false);

  // A control is only fillable if a person could see it. Honeypot fields are hidden on purpose;
  // filling one is how a bot gets caught. Custom-styled file/checkbox/radio inputs are often
  // visually hidden behind a visible label or button, so those get a little more latitude.
  const fillable = (el, type) => {
    if (type === "file") return shown(el) || labelShown(el) || shown(el.parentElement);
    if (type === "checkbox" || type === "radio") return shown(el) || labelShown(el);
    if (el.closest('[aria-hidden="true"]')) return false;
    return shown(el);
  };

  const byIds = (ids) =>
    (ids || "")
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => textOf(document.getElementById(id)))
      .filter(Boolean)
      .join(" ");

  const ariaLabel = (el) => clean(el.getAttribute("aria-label")) || byIds(el.getAttribute("aria-labelledby"));

  const ownLabel = (el) =>
    labelsOf(el)
      .map(textOf)
      .filter(Boolean)
      .join(" ");

  const legendOf = (el) => {
    const fs = el.closest("fieldset");
    if (fs) {
      const legend = Array.from(fs.children).find((c) => c.tagName === "LEGEND");
      if (legend) return textOf(legend);
    }
    const group = el.closest('[role="group"], [role="radiogroup"]');
    if (group) return ariaLabel(group);
    return "";
  };

  const nearbyOf = (el) => {
    let node = el;
    for (let depth = 0; depth < 3 && node && node.parentElement; depth++) {
      let prev = node.previousElementSibling;
      while (prev && prev.matches("input, select, textarea, script, style, br")) prev = prev.previousElementSibling;
      if (prev) {
        const t = textOf(prev);
        if (t && t.length <= 200) return t;
      }
      node = node.parentElement;
    }
    return "";
  };

  const hintOf = (el) => byIds(el.getAttribute("aria-describedby"));

  const looksRequired = (el, labelText) =>
    el.required ||
    el.getAttribute("aria-required") === "true" ||
    labelText.includes("*") ||
    /\(required\)/i.test(labelText);

  const kindOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === "select") return "select";
    if (tag === "textarea") return "textarea";
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (["text", "search", ""].includes(t)) return "text";
    if (["email", "tel", "url", "number", "date", "file", "checkbox", "radio"].includes(t)) return t;
    if (["datetime-local", "month", "week", "time"].includes(t)) return "date";
    return null; // hidden, submit, button, reset, image, password, color, range ...
  };

  const warnings = [];
  const all = Array.from(document.querySelectorAll("input, select, textarea"));
  if (all.some((el) => (el.getAttribute("type") || "").toLowerCase() === "password")) {
    warnings.push("A password field was found and left alone; sign in yourself if the site requires it.");
  }

  const candidates = all.filter((el) => {
    if (el.disabled || el.readOnly) return false;
    if (el.closest('form[role="search"], [role="search"]')) return false;
    const kind = kindOf(el);
    if (!kind || kind === "search") return false;
    if (kind === "text" && (el.getAttribute("type") || "").toLowerCase() === "search") return false;
    return fillable(el, kind);
  });

  // Group controls by the form that owns them; formless controls form one pseudo-form.
  const groups = new Map();
  for (const el of candidates) {
    const key = el.form || null;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(el);
  }
  const score = (els) =>
    els.length + (els.some((e) => kindOf(e) === "file") ? 5 : 0) + (els.some((e) => kindOf(e) === "textarea") ? 3 : 0);
  let chosenKey = null;
  let chosen = [];
  let best = -1;
  for (const [key, els] of groups) {
    if (score(els) > best) {
      best = score(els);
      chosenKey = key;
      chosen = els;
    }
  }
  if (groups.size > 1) warnings.push("Several forms were found; the one with the most fields was used.");

  // Build fields, grouping radios (and multi-checkbox groups) by name.
  const fields = [];
  const handled = new Set();
  let counter = 0;
  // The lowest element containing every member of a group: the question text sits just before it.
  const lca = (els) => {
    let a = els[0].parentElement;
    while (a && !els.every((e) => a.contains(e))) a = a.parentElement;
    return a || els[0];
  };
  // "<input type=radio> Yes": the label is a bare text node right after the control.
  const nextText = (el) => {
    const n = el.nextSibling;
    return n && n.nodeType === Node.TEXT_NODE ? clean(n.textContent) : "";
  };
  const optionOf = (el, k, fieldId) => {
    el.setAttribute("data-oa-id", fieldId + "-o" + k);
    return {
      value: el.value || "",
      label: ownLabel(el) || clean(el.getAttribute("aria-label")) || nextText(el) || clean(el.value),
      element_id: fieldId + "-o" + k,
      checked: !!el.checked,
    };
  };

  for (const el of chosen) {
    if (handled.has(el)) continue;
    if (fields.length >= MAX_FIELDS) {
      warnings.push("The form has more than " + MAX_FIELDS + " fields; the rest were ignored.");
      break;
    }
    const kind = kindOf(el);
    const id = "oa-" + counter++;
    const base = {
      id,
      kind,
      name: clean(el.getAttribute("name") || el.id),
      aria_label: ariaLabel(el),
      legend: legendOf(el),
      placeholder: clean(el.getAttribute("placeholder")),
      nearby: nearbyOf(el),
      hint: hintOf(el),
      required: false,
      current_value: null,
      options: [],
      max_length: null,
      accept: null,
      numeric: false,
      label: "",
    };

    if (kind === "radio" || kind === "checkbox") {
      const name = el.getAttribute("name");
      const sameName =
        name && kind === "radio"
          ? chosen.filter((o) => kindOf(o) === "radio" && o.getAttribute("name") === name && o.form === el.form)
          : name
          ? chosen.filter((o) => kindOf(o) === "checkbox" && o.getAttribute("name") === name && o.form === el.form)
          : [el];
      const members = sameName.length ? sameName : [el];
      if (kind === "radio" || members.length > 1) {
        base.options = members.map((m, k) => optionOf(m, k, id));
        base.nearby = nearbyOf(lca(members)) || base.nearby;
        members.forEach((m) => handled.add(m));
        base.label = base.legend || base.aria_label || "";
        base.kind = kind;
        base.required = members.some((m) => looksRequired(m, ownLabel(m)));
        const on = base.options.filter((o) => o.checked).map((o) => o.value);
        base.current_value = on.length ? on.join("|") : null;
      } else {
        el.setAttribute("data-oa-id", id);
        base.label = ownLabel(el) || nextText(el);
        base.required = looksRequired(el, base.label);
        base.current_value = el.checked ? "true" : null;
      }
    } else {
      el.setAttribute("data-oa-id", id);
      base.label = ownLabel(el);
      base.required = looksRequired(el, base.label || base.aria_label);
      if (kind === "select") {
        base.options = Array.from(el.options)
          .slice(0, MAX_OPTIONS)
          .map((o) => ({ value: o.value, label: clean(o.textContent).slice(0, 80), element_id: null, checked: o.selected }));
        const sel = el.selectedOptions && el.selectedOptions[0];
        base.current_value = sel && sel.value !== "" ? sel.value : null;
      } else if (kind === "file") {
        base.accept = clean(el.getAttribute("accept")) || null;
      } else {
        base.current_value = el.value ? clean(el.value) : null;
        if (kind === "number" || el.getAttribute("inputmode") === "numeric") base.numeric = true;
      }
      const max = el.maxLength;
      if (max && max > 0 && max < 100000) base.max_length = max;
    }
    handled.add(el);
    fields.push(base);
  }

  // The submit control of the chosen form.
  const submitMatch = /apply|submit|send/i;
  const buttonPool = chosenKey
    ? Array.from(chosenKey.querySelectorAll("button, input[type=submit], input[type=image]"))
    : Array.from(document.querySelectorAll("button, input[type=submit]"));
  const usable = buttonPool.filter((b) => !b.disabled && shown(b));
  const labelOfButton = (b) => clean(b.tagName === "INPUT" ? b.value : b.textContent);
  const explicit = usable.find((b) => (b.getAttribute("type") || (b.tagName === "BUTTON" ? "submit" : "")).toLowerCase() === "submit");
  const byText = usable.find((b) => submitMatch.test(labelOfButton(b)));
  const submit = chosenKey ? explicit || byText : byText;
  if (submit) submit.setAttribute("data-oa-submit", "1");
  const effectiveAction = (button, form) => {
    if (button && button.hasAttribute("formaction")) return button.formAction || null;
    return form && form.action ? form.action : null;
  };

  if (fields.length === 0 && document.querySelectorAll("iframe").length > 0) {
    warnings.push("No form fields were found, but the page has embedded frames; the application may be inside one (not supported yet).");
  }

  return {
    url: location.href,
    title: clean(document.title),
    // Where clicking the submit control will really send the data: a button's own formaction
    // overrides the form's action. (forms.py re-checks this at the moment of the click.)
    form_action: effectiveAction(submit, chosenKey),
    submit_label: submit ? labelOfButton(submit) : null,
    fields,
    warnings,
  };
}
