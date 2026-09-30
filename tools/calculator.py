"""计算器 - 安全求值"""
import ast
import math
import operator


# 允许的运算符
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# 允许的函数
_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.BinOp):
        op = _OPS.get(type(node.op))
        if not op: raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    elif isinstance(node, ast.UnaryOp):
        op = _OPS.get(type(node.op))
        if not op: raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            args = [_safe_eval(a) for a in node.args]
            return _FUNCS[node.func.id](*args)
        raise ValueError(f"不支持的函数调用")
    elif isinstance(node, ast.Name):
        if node.id in _FUNCS:
            return _FUNCS[node.id]
        raise ValueError(f"未知标识符: {node.id}")
    elif isinstance(node, ast.Tuple):
        return tuple(_safe_eval(e) for e in node.elts)
    elif isinstance(node, ast.List):
        return [_safe_eval(e) for e in node.elts]
    else:
        raise ValueError(f"不支持的语法: {type(node).__name__}")


def calculate(expression):
    """安全求值数学表达式"""
    if not expression or not expression.strip():
        return "表达式为空"
    expr = expression.strip()

    # 常见替换（让用户说话更自然）
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = expr.replace("，", ",").replace("（", "(").replace("）", ")")

    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree.body)
        # 如果是浮点数且是整数，去掉 .0
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        # 保留合理精度
        if isinstance(result, float):
            result = round(result, 6)
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算失败：{expression}（{e}）"