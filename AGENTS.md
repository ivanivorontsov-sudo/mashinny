# Инструкция для ИИ-агентов / Agent guide

1. Пиши код в компактном или каноническом виде, который принимает грамматика `mash grammar` (GBNF). Сразу проверяй через `mash check файл.pn`: ответ приходит в JSON, а у ошибки есть `code`, `path`, `at`, `expected`, `got` и часто `fix`.
2. Рекурсии нужна мера `dec`. Циклов нет: используй `map`, `filter`, `fold` и `range`.
3. Для печати нужен эффект `!io` у функции.
4. Перед запуском вызывай `mash predict` — он даёт точный результат, число шагов и эффекты без побочных действий.
5. `mash prove` доказывает контракты и завершимость; в `counterexample` приходят значения, на которых контракт ломается.
6. Храни и сравнивай программы как `.pnb` и `semantic_hash`.

Write code accepted by `mash grammar`; validate with `mash check` (JSON errors with `fix`); recursion needs `dec`; effects need `!io`; use `mash predict` for exact results and `mash prove` for Z3 proofs.
