#!/usr/bin/env python3
"""Корпус обещаний бота из кода — слой 1 для карты агента.

AST, не регэксп. Три бакета:
  - ВХОДЫ: команды (Command/CommandStart), F.text == "...", startswith/regexp;
  - КНОПКИ: InlineKeyboardButton/KeyboardButton(text=...), с разрешением словарей/констант модуля;
  - ТЕКСТЫ: аргументы answer/reply/send_message/edit_text/... и module-level константы
    с кириллицей (шаблоны, HELP/START/STRANGER).
f-строки печатаются с плейсхолдерами {…}. Что не удалось разрешить — отдельный список с адресами:
скрипт обязан честно сказать, чего он не видит (иначе он сам совершает дыру «ложное не нашёл»).

Использование: extract_promises.py <корень проекта> [--md] [--full]
"""
import argparse
import ast
import os
import re
import sys

# Known text signatures. Media captions vary by Telegram SDK, so only named
# captions are accepted; positional media identifiers must never become promises.
TEXT_POSITIONS = {"answer": 0, "reply": 0, "reply_text": 0, "edit_text": 0,
                  "send_message": 1, "edit_message_text": 0, "edit_caption": 0}
MEDIA_ATTRS = {f"{prefix}_{kind}" for prefix in ("answer", "reply", "send")
               for kind in ("photo", "voice", "document", "animation", "video", "audio")}
CUSTOM_ATTRS = {"send_rich_message", "deliver", "send_long"}
SEND_ATTRS = set(TEXT_POSITIONS) | MEDIA_ATTRS | CUSTOM_ATTRS
BUTTON_NAMES = {"InlineKeyboardButton", "KeyboardButton"}
CMD_NAMES = {"Command", "CommandStart"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "tests", "test", "migrations",
             "scripts", "data", "var", "assets", "backups", "logs"}
CYR = re.compile(r"[А-Яа-яЁё]")


def py_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(d, f)


class Module:
    def __init__(self, path, root):
        self.path = path
        self.rel = os.path.relpath(path, root)
        self.src = open(path, encoding="utf-8", errors="replace").read()
        self.tree = ast.parse(self.src, filename=path)
        self.consts = {}   # NAME -> rendered string
        self.dicts = {}    # NAME -> {key: rendered}
        for node in self.tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets, value = [node.target], node.value
            else:
                continue
            for t in targets:
                s = render(value)
                if s is not None:
                    self.consts[t.id] = s
                elif isinstance(value, ast.Dict):
                    d = {}
                    for k, v in zip(value.keys, value.values):
                        ks, vs = render(k), render(v)
                        if ks is not None and vs is not None:
                            d[ks] = vs
                    if d:
                        self.dicts[t.id] = d


def render(node):
    """Строка из литерала / f-строки / конкатенации / .join над литералами; иначе None."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                out.append("{" + ast.unparse(v.value)[:30] + "}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = render(node.left), render(node.right)
        if a is not None and b is not None:
            return a + b
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        base = render(node.func.value)
        return base
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
        sep = render(node.func.value)
        if sep is not None and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
            parts = [render(e) for e in node.args[0].elts]
            if all(p is not None for p in parts):
                return sep.join(parts)
    return None


def resolve(node, mod):
    """render + разрешение имён и словарей модуля."""
    s = render(node)
    if s is not None:
        return s
    if isinstance(node, ast.Name):
        return mod.consts.get(node.id)
    if isinstance(node, ast.Attribute):  # persona.HELP
        return None  # межмодульные ссылки — в «нерезолвимые», дочитывает инспектор
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        d = mod.dicts.get(node.value.id)
        key = render(node.slice)
        if d and key is not None:
            return d.get(key)
    return None


def short(s, n=110):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--md", action="store_true")
    parser.add_argument("--full", action="store_true", help="Полный текст без сокращений, включая конец шаблона")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        parser.error("корень проекта должен быть существующим каталогом")
    md = args.md
    display = (lambda value, limit=110: value.replace("\n", "\n    ")) if args.full else short
    entries, buttons, texts, consts, unresolved = [], [], [], [], []
    mods = []
    for path in py_files(root):
        try:
            mods.append(Module(path, root))
        except SyntaxError as e:
            unresolved.append((os.path.relpath(path, root), 0, f"SyntaxError: {e}"))
    for mod in mods:
        for name, s in mod.consts.items():
            if CYR.search(s) and (name.isupper() or len(s) > 60):
                consts.append((mod.rel, name, s))
        for node in ast.walk(mod.tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            line = node.lineno
            if fname in CMD_NAMES:
                for a in node.args:
                    s = render(a)
                    entries.append((mod.rel, line, "/" + s if s else "/start" if fname == "CommandStart" else "?"))
                if not node.args and fname == "CommandStart":
                    entries.append((mod.rel, line, "/start"))
            elif fname in BUTTON_NAMES:
                arg = None
                for kw in node.keywords:
                    if kw.arg == "text":
                        arg = kw.value
                if arg is None and node.args:
                    arg = node.args[0]
                s = resolve(arg, mod) if arg is not None else None
                if s is None:
                    unresolved.append((mod.rel, line, "кнопка: " + (ast.unparse(arg)[:60] if arg is not None else "?")))
                else:
                    buttons.append((mod.rel, line, s))
            elif fname in SEND_ATTRS:
                arg = None
                for kw in node.keywords:
                    if kw.arg in ("text", "caption"):
                        arg = kw.value
                if arg is None:
                    if fname in CUSTOM_ATTRS:
                        unresolved.append((mod.rel, line, f"{fname}: неизвестная сигнатура обёртки; проверить текстовый аргумент"))
                        continue
                    if fname in MEDIA_ATTRS:
                        if len(node.args) > (2 if fname.startswith("send_") else 1) or any(kw.arg is None for kw in node.keywords):
                            unresolved.append((mod.rel, line, f"{fname}: позиционные параметры медиа или **kwargs; проверить caption по SDK"))
                        continue  # no explicit caption; file id/path is not text
                    position = TEXT_POSITIONS[fname]
                    if any(isinstance(a, ast.Starred) for a in node.args) or len(node.args) <= position:
                        unresolved.append((mod.rel, line, f"{fname}: текстовый аргумент не определён (проверить сигнатуру/kwargs)"))
                        continue
                    arg = node.args[position]
                s = resolve(arg, mod)
                if s is None:
                    unresolved.append((mod.rel, line, f"{fname}: динамический/неподдерживаемый текст: " + ast.unparse(arg)[:60]))
                else:
                    texts.append((mod.rel, line, s))
            elif isinstance(fn, ast.Attribute) and fn.attr in ("startswith", "regexp", "lower") and \
                    isinstance(fn.value, ast.Attribute) and fn.value.attr == "text":
                for a in node.args:
                    s = render(a)
                    if s:
                        entries.append((mod.rel, line, "text~" + s))
        # F.text == "..."
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) and node.left.attr == "text":
                for c in node.comparators:
                    s = resolve(c, mod)
                    if s:
                        entries.append((mod.rel, node.lineno, "text==" + s))

    total = len(entries) + len(buttons) + len(texts) + len(consts)
    cov = total / (total + len(unresolved)) * 100 if total + len(unresolved) else 0
    h = (lambda t: f"\n## {t}\n") if md else (lambda t: f"\n=== {t} ===\n")
    print(f"Корпус обещаний: {root}")
    print(f"Файлов: {len(mods)} · входов {len(entries)} · кнопок {len(buttons)} · текстов {len(texts)} · "
          f"констант {len(consts)} · НЕ СМОГ РАЗРЕШИТЬ: {len(unresolved)} мест ({cov:.0f}% разрешённых обнаруженных кандидатов)")
    print("Это доля разрешения обнаруженных кандидатов, НЕ покрытие всех обещаний. Имена методов эвристические; сигнатуры необходимо сверить с SDK. f-строки — шаблоны, значения подстановок не проверены.")
    print(h("ВХОДЫ (команды, фразы)"))
    for rel, line, s in sorted(set(entries)):
        print(f"- {rel}:{line}  {s}")
    print(h("КНОПКИ"))
    for rel, line, s in buttons:
        print(f"- {rel}:{line}  {display(s, 60)}")
    print(h("ТЕКСТЫ (что читает человек)"))
    for rel, line, s in texts:
        print(f"- {rel}:{line}  {display(s)}")
    print(h("КОНСТАНТЫ-ШАБЛОНЫ (module-level, кириллица)"))
    for rel, name, s in consts:
        print(f"- {rel}  {name}  ({len(s)} зн.)  {display(s, 80)}")
    print(h("НЕ СМОГ РАЗРЕШИТЬ — дочитать глазами"))
    for rel, line, s in unresolved:
        print(f"- {rel}:{line}  {s}")
    print("\nСлой 2 (промпты/персона: обещания второго порядка) и слой 3 (реальные ответы из базы) "
          "этот скрипт не покрывает — см. references/map.md §6.")


if __name__ == "__main__":
    main()
