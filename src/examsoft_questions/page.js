// Scripts run in an ExamSoft question editor (/STW-war/ei/question/<type>/create|edit).
// The editor is jQuery 1.9 + CKEditor 3.6 + fancytree; each command reads or drives it
// through the page's own objects so that its validation and server model stay in step.
({ command, ...args }) => {
  const editor = (id) => CKEDITOR.instances[id];
  const ready = (id) => { const e = editor(id); return !!(e && e.mode === "wysiwyg" && e.document); };
  const text = (html) => {
    const div = document.createElement("div");
    // Space block boundaries, which textContent would run together, as a reader sees them.
    div.innerHTML = html.replace(/<\/(p|div|li|h\d|tr|td)>|<br\s*\/?>/gi, " $&");
    return div.textContent.replace(/\s+/g, " ").trim();
  };
  const choiceRows = () => [...document.querySelectorAll("#mcqChoices tr.mcqRow")];
  const blankRows = () =>
    [...document.querySelectorAll("#blanksTable input[name='blankTypes[]']")].map((input) => input.closest("tr"));
  const findFolder = () => {
    const tree = window.eitree && eitree.get("selectFolder");
    return tree ? tree.getNodeByKey(args.key) : null;
  };

  switch (command) {
    case "loaded":
      return !!(window.jQuery && window.EIUtil && window.CKEDITOR && ready("questionRichText") &&
        Object.keys(CKEDITOR.instances).every(ready) &&
        choiceRows().every((row) => ready(row.getAttribute("choiceuid"))) &&
        // An edit page builds a saved case study's tabs after its editors are ready.
        document.querySelectorAll("input[name='caseStudyTitle[]']").length >=
          ((window.caseStudyObject && caseStudyObject.length) || 0) &&
        document.querySelector("#createQuestionForm"));

    case "install":
      // Leaving the editor must not raise the unsaved-changes prompt, and messages the
      // editor shows (client validation, EI_ERROR responses) are kept for the client.
      window.onbeforeunload = null;
      jQuery(window).off("beforeunload");
      if (!EIUtil.__examsoftQuestions) {
        const show = EIUtil.showMessages;
        window.__examsoftMessages = [];
        EIUtil.showMessages = function (messages) {
          const list = jQuery.isArray(messages) ? messages : [messages];
          for (const m of list) {
            const message = m && typeof m === "object" ? m.message : m;
            if (message) window.__examsoftMessages.push(text(String(message)));
          }
          return show.apply(this, arguments);
        };
        EIUtil.__examsoftQuestions = true;
      }
      localStorage.removeItem("/STW-war/ei/questionstree/editablefolders.treeDataHolder");
      return true;

    case "messages":
      return window.__examsoftMessages || [];

    case "editorsReady":
      return args.ids.every(ready);

    case "setRich":
      // CKEditor 3 sets data asynchronously, and the editor copies its textareas back
      // into the editors after adding or removing a choice; keep both in step.
      return new Promise((resolve) => {
        const textarea = document.getElementById(args.id);
        if (textarea) textarea.value = args.html;
        editor(args.id).setData(args.html, () => resolve(true));
      });

    case "richTexts":
      return args.ids.map((id) => (editor(id) ? text(editor(id).getData()) : null));

    case "folderLoaded":
      return !!findFolder();

    case "selectFolder":
      findFolder().setActive(true);
      return true;

    case "choiceIds":
      return choiceRows().map((row) => row.getAttribute("choiceuid"));

    case "blanks":
      return blankRows().map((row) => ({
        type: row.querySelector("input[name='blankTypes[]']").value,
        sequence: row.querySelector("td.letter").textContent.trim(),
        values: [...row.querySelectorAll("[name='blankTexts[]']")].map((e) => e.value),
      }));

    case "text":
      return text(args.html);

    case "post":
      return new Promise((resolve) =>
        EIUtil._post(args.url, "json", null, args.payload == null ? null : JSON.stringify(args.payload), null,
          (data) => resolve(data),
          (xhr, status, error) => resolve({ status: "HTTP_ERROR", messages: [String(status) + " " + String(error)] }),
          null));

    case "search":
      // The keyword search stores the query in the session; its grid then pages the results.
      return new Promise((resolve) => {
        const query = { omniBasicSearchInput: args.term, selectedCategoryUIDs: [], selectedFolderUIDs: [] };
        EIUtil._post("/STW-war/ei/questions/search", "json", null, JSON.stringify(query), null,
          (data) => {
            if (!data || data.status !== "EI_OK") return resolve(data || { status: "EMPTY" });
            fetch(data.reloadUrl + "?sEcho=1&iDisplayStart=0&iDisplayLength=250",
              { credentials: "include", headers: { "X-Requested-With": "XMLHttpRequest" } })
              .then((response) => response.json())
              .then((grid) => resolve({ status: "EI_OK", rows: grid.mData || [] }))
              .catch((error) => resolve({ status: "FETCH_ERROR", messages: [String(error)] }));
          },
          (xhr, status, error) => resolve({ status: "HTTP_ERROR", messages: [String(status) + " " + String(error)] }),
          null);
      });

    case "tree":
      return fetch("/STW-war/ei/questionstree/editablefolders", { credentials: "include" })
        .then((response) => response.json());

    case "state": {
      const checked = (selector) => { const e = document.querySelector(selector); return e ? e.checked : null; };
      const value = (selector) => { const e = document.querySelector(selector); return e ? e.value : null; };
      return {
        itemId: value("#questionId"),
        revision: value("#revNum"),
        title: value("#displayText"),
        folderKey: value("#folderUID"),
        folderName: (document.querySelector("#selectedQuestionFolder") || {}).textContent?.trim() ?? "",
        weight: value("#weight"),
        group: value("#randomGroup"),
        cutScore: value("#cutScore"),
        rationale: value("#comment"),
        charLimit: value("#essayCharLim"),
        stem: text(editor("questionRichText").getData()),
        stemBlanks: (editor("questionRichText").getData().match(/blanks\/blank_\d+\.jpg/g) || []).length,
        options: {
          partial: checked("#proportionateScoringMC") ?? checked("#proportionateScoring1"),
          allThatApply: checked("#enableAllThatApplyMC"),
          plusMinus: checked("#plusMinusScoringMC"),
          randomize: checked("#randomizeChoices1"),
          graphing: checked("#graphingCalculatorChk"),
          scientific: checked("#scientificCalculatorChk"),
          spreadsheet: checked("#spreadsheetChk"),
        },
        choices: choiceRows().map((row) => ({
          text: text(editor(row.getAttribute("choiceuid")).getData()),
          correct: row.querySelector("input[name='correctBool[]']").checked,
          locked: row.querySelector("input[name='locked[]']").checked,
        })),
        caseStudy: [...document.querySelectorAll("input[name='caseStudyTitle[]']")].map((input) => {
          const content = document.getElementById(input.id.replace("caseStudy-title", "caseStudy-fragment-"));
          return { title: input.value, text: content && editor(content.id) ? text(editor(content.id).getData()) : null };
        }),
        blanks: blankRows().map((row) => ({
          type: row.querySelector("input[name='blankTypes[]']").value,
          values: [...row.querySelectorAll("[name='blankTexts[]']")].map((e) => e.value),
        })),
      };
    }

    default:
      throw new Error("unknown command " + command);
  }
}
