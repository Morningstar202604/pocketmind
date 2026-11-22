import { useEffect, useRef, useState } from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { oneDark } from "@codemirror/theme-one-dark";

async function languageFor(path: string): Promise<Extension> {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  if (ext === "js" || ext === "jsx" || ext === "mjs" || ext === "cjs") {
    const { javascript } = await import("@codemirror/lang-javascript");
    return javascript({ jsx: ext === "jsx" });
  }
  if (ext === "ts" || ext === "tsx") {
    const { javascript } = await import("@codemirror/lang-javascript");
    return javascript({ typescript: true, jsx: ext === "tsx" });
  }
  if (ext === "py") {
    const { python } = await import("@codemirror/lang-python");
    return python();
  }
  if (ext === "json" || ext === "jsonc") {
    const { json } = await import("@codemirror/lang-json");
    return json();
  }
  if (ext === "md" || ext === "markdown") {
    const { markdown } = await import("@codemirror/lang-markdown");
    return markdown();
  }
  if (ext === "html" || ext === "htm") {
    const { html } = await import("@codemirror/lang-html");
    return html();
  }
  if (ext === "css") {
    const { css } = await import("@codemirror/lang-css");
    return css();
  }
  return [];
}

/**
 * 工作区编辑器：CodeMirror 6。
 *
 * 只负责「显示 + 编辑 + Ctrl/Cmd+S」；保存前的 diff 确认、文件读写仍由
 * WorkspacePage 管。这样编辑器可以独立替换/测试，也不会把 CodeMirror 的
 * 状态和文件状态搅在一起。
 */
const editorTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "transparent",
    color: "var(--text)",
    fontSize: "13px",
  },
  ".cm-scroller": {
    fontFamily: "ui-monospace, Menlo, Consolas, monospace",
    lineHeight: "1.55",
    overflow: "auto",
  },
  ".cm-gutters": {
    backgroundColor: "var(--panel)",
    color: "var(--text-3)",
    border: "none",
  },
  ".cm-activeLine": {
    backgroundColor: "color-mix(in srgb, var(--brand) 8%, transparent)",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "var(--brand-text)",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in srgb, var(--brand) 25%, transparent) !important",
  },
});

export function WorkspaceEditor({
  path,
  value,
  dark,
  jumpLine,
  onChange,
  onSave,
}: {
  path: string;
  value: string;
  dark: boolean;
  jumpLine?: number | null;
  onChange: (next: string) => void;
  onSave: () => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const [langCompartment] = useState(() => new Compartment());
  const loadIdRef = useRef(0);
  const valueRef = useRef(value);
  const changeRef = useRef(onChange);
  const saveRef = useRef(onSave);
  valueRef.current = value;
  changeRef.current = onChange;
  saveRef.current = onSave;

  useEffect(() => {
    if (!hostRef.current) return;
    const extensions = [
      basicSetup,
      EditorView.lineWrapping,
      editorTheme,
      keymap.of([
        {
          key: "Mod-s",
          run: () => {
            saveRef.current();
            return true;
          },
        },
      ]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) changeRef.current(update.state.doc.toString());
      }),
      langCompartment.of([]),
    ];
    if (dark) extensions.push(oneDark);
    const state = EditorState.create({ doc: valueRef.current, extensions });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [path, dark, langCompartment]);

  useEffect(() => {
    const loadId = ++loadIdRef.current;
    let alive = true;
    void languageFor(path)
      .then((ext) => {
        const view = viewRef.current;
        if (!alive || !view || loadId !== loadIdRef.current) return;
        view.dispatch({ effects: langCompartment.reconfigure(ext) });
      })
      .catch(() => {
        /* 语言包加载失败时保持纯文本，不影响编辑 */
      });
    return () => {
      alive = false;
    };
  }, [path, dark, langCompartment]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (value !== current) {
      view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
    }
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || !jumpLine) return;
    const line = view.state.doc.line(Math.max(1, Math.min(jumpLine, view.state.doc.lines)));
    view.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
    view.focus();
  }, [jumpLine]);

  return <div ref={hostRef} className="ws-editor" aria-label="文件内容" />;
}
