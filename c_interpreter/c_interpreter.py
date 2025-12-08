import tkinter as tk
from tkinter import scrolledtext, Toplevel
import sys

# ==========================================
# 1. 基盤: メモリと型の定義
# ==========================================

INT_SIZE = 4 # int型とポインタのアドレスサイズ
CHAR_SIZE = 1 # char型のデータサイズ

class Memory:
    def __init__(self):
        self.heap = {} 
        self.next_free_address = 1000

    def allocate(self, size_in_bytes):
        addr = self.next_free_address
        self.next_free_address += size_in_bytes
            
        for i in range(size_in_bytes):
            self.heap[addr + i] = 0
        return addr

    def set_int_value(self, address, value):
        if address < 1000: raise Exception(f"無効なアドレス ({address}) への書き込み試行")
        for i in range(INT_SIZE):
            byte_value = (value >> (i * 8)) & 0xFF
            self.heap[address + i] = byte_value

    def get_int_value(self, address):
        if address not in self.heap:
            raise Exception(f"無効なアドレス ({address}) または未初期化データへのアクセス")
        
        value = 0
        for i in range(INT_SIZE):
            byte_value = self.heap.get(address + i, 0)
            value |= (byte_value << (i * 8))
        return value

    def set_byte_value(self, address, byte_value):
        if address < 1000: raise Exception(f"無効なアドレス ({address}) への書き込み試行")
        if not (0 <= byte_value <= 255): raise ValueError("バイト値は0-255の範囲である必要があります。")
        self.heap[address] = byte_value

    def get_byte_value(self, address):
        if address not in self.heap:
            return 0 
        return self.heap[address]

class Symbol:
    def __init__(self, var_type, address): self.type, self.address = var_type, address
    def __repr__(self): return f"Symbol(type='{self.type}', addr={self.address})"

class ReturnSignal(Exception):
    def __init__(self, value): self.value = value

# ==========================================
# 2. 字句解析 (Lexer)
# ==========================================

TT_INT, TT_STRING, TT_ID, TT_PLUS, TT_MINUS, TT_MUL, TT_DIV, TT_ASSIGN = 'INT', 'STRING', 'ID', 'PLUS', 'MINUS', 'MUL', 'DIV', 'ASSIGN'
TT_EQ, TT_NEQ, TT_LT, TT_GT = 'EQ', 'NEQ', 'LT', 'GT'
TT_SEMICOLON, TT_LPAREN, TT_RPAREN, TT_LBRACE, TT_RBRACE, TT_COMMA, TT_KEYWORD, TT_EOF = 'SEMICOLON', 'LPAREN', 'RPAREN', 'LBRACE', 'RBRACE', 'COMMA', 'KEYWORD', 'EOF'
TT_AMPERSAND, TT_ASTERISK = 'AMPERSAND', 'ASTERISK'

class Token:
    def __init__(self, type, value=None): self.type, self.value = type, value
    def __repr__(self): return f"Token({self.type}, {self.value})"

class Lexer:
    def __init__(self, text): self.text, self.pos, self.current_char = text, -1, None; self.advance()
    def advance(self): self.pos += 1; self.current_char = self.text[self.pos] if self.pos < len(self.text) else None
    def peek(self):
        peek_pos = self.pos + 1
        if peek_pos < len(self.text): return self.text[peek_pos]
        return Token(TT_EOF)

    def skip_comment(self):
        while self.current_char is not None and self.current_char != '\n': self.advance()
    def skip_multiline_comment(self):
        while self.current_char is not None:
            if self.current_char == '*' and self.peek() == '/': self.advance(); self.advance(); return
            self.advance()

    def make_tokens(self):
        tokens = []
        while self.current_char is not None:
            if self.current_char.isspace(): self.advance()
            elif self.current_char.isdigit(): tokens.append(self._make_number())
            elif self.current_char.isalpha() or self.current_char == '_': tokens.append(self._make_identifier())
            
            elif self.current_char == '"':
                self.advance()
                tokens.append(self._make_string())
            
            elif self.current_char == '/':
                if self.peek() == '/': self.advance(); self.advance(); self.skip_comment(); continue
                elif self.peek() == '*': self.advance(); self.advance(); self.skip_multiline_comment(); continue
                else: tokens.append(Token(TT_DIV)); self.advance()
            
            elif self.current_char == '&': tokens.append(Token(TT_AMPERSAND)); self.advance()
            elif self.current_char == '*': tokens.append(Token(TT_ASTERISK)); self.advance()
            
            elif self.current_char == '=':
                if self.peek() == '=': self.advance(); self.advance(); tokens.append(Token(TT_EQ))
                else: self.advance(); tokens.append(Token(TT_ASSIGN))
            elif self.current_char == '!':
                if self.peek() == '=': self.advance(); self.advance(); tokens.append(Token(TT_NEQ))
                else: raise Exception("不正な文字: '!'")
            elif self.current_char == '<': tokens.append(Token(TT_LT)); self.advance()
            elif self.current_char == '>': tokens.append(Token(TT_GT)); self.advance()
            elif self.current_char == '+': tokens.append(Token(TT_PLUS)); self.advance()
            elif self.current_char == '-': tokens.append(Token(TT_MINUS)); self.advance()
            elif self.current_char == ';': tokens.append(Token(TT_SEMICOLON)); self.advance()
            elif self.current_char == '(': tokens.append(Token(TT_LPAREN)); self.advance()
            elif self.current_char == ')': tokens.append(Token(TT_RPAREN)); self.advance()
            elif self.current_char == '{': tokens.append(Token(TT_LBRACE)); self.advance()
            elif self.current_char == '}': tokens.append(Token(TT_RBRACE)); self.advance()
            elif self.current_char == ',': tokens.append(Token(TT_COMMA)); self.advance()
            else: raise Exception(f"不正な文字: {self.current_char}")
        
        tokens.append(Token(TT_EOF))
        return tokens

    def _make_number(self):
        num_str = ''
        while self.current_char is not None and self.current_char.isdigit(): num_str += self.current_char; self.advance()
        return Token(TT_INT, int(num_str))

    def _make_identifier(self):
        id_str = ''; KEYWORDS = ['int', 'void', 'return', 'print', 'debug', 'if', 'else', 'char']
        while self.current_char is not None and (self.current_char.isalnum() or self.current_char == '_'): id_str += self.current_char; self.advance()
        return Token(TT_KEYWORD, id_str) if id_str in KEYWORDS else Token(TT_ID, id_str)

    def _make_string(self):
        str_val = ""
        while self.current_char is not None and self.current_char != '"':
            str_val += self.current_char
            self.advance()
        
        if self.current_char != '"':
            raise Exception("文字列が閉じられていません。")
        
        self.advance()
        return Token(TT_STRING, str_val)

# ==========================================
# 3. AST & 4. Parser
# ==========================================

class ASTNode: pass
class ProgramNode(ASTNode):
    def __init__(self, nodes): self.nodes = nodes
class VarDeclNode(ASTNode):
    def __init__(self, var_type, name): self.var_type, self.name = var_type, name
class FunctionDefNode(ASTNode):
    def __init__(self, return_type, name, params, body): self.return_type, self.name, self.params, self.body = return_type, name, params, body
class FunctionCallNode(ASTNode):
    def __init__(self, name, args): self.name, self.args = name, args
class ReturnNode(ASTNode):
    def __init__(self, expr): self.expr = expr
class AssignmentNode(ASTNode):
    def __init__(self, target, expr): self.target, self.expr = target, expr
class BinaryOpNode(ASTNode):
    def __init__(self, left, op, right): self.left, self.op, self.right = left, op, right
class NumberNode(ASTNode):
    def __init__(self, value): self.value = value
class StringNode(ASTNode):
    def __init__(self, value): self.value = value
class VarAccessNode(ASTNode):
    def __init__(self, name): self.name = name
class PrintNode(ASTNode):
    def __init__(self, expr): self.expr = expr
class DebugNode(ASTNode): pass
class IfNode(ASTNode):
    def __init__(self, condition, true_body, false_body=None): self.condition, self.true_body, self.false_body = condition, true_body, false_body
class UnaryOpNode(ASTNode):
    def __init__(self, op, operand): self.op, self.operand = op, operand

class Parser:
    def __init__(self, tokens): self.tokens, self.token_idx = tokens, -1; self.advance()
    def advance(self):
        self.token_idx += 1
        if self.token_idx < len(self.tokens): self.current_token = self.tokens[self.token_idx]
        else: self.current_token = Token(TT_EOF)
    def peek(self):
        if self.token_idx + 1 < len(self.tokens): return self.tokens[self.token_idx + 1]
        return Token(TT_EOF)

    def parse(self):
        nodes = []
        while self.current_token.type != TT_EOF:
            if self.current_token.type == TT_KEYWORD and (self.current_token.value in ('int', 'void', 'char')):
                nodes.append(self.top_level_declaration())
            else: raise Exception(f"予期しないトークン: {self.current_token}")
        return ProgramNode(nodes)

    def get_type_string(self):
        full_type = self.current_token.value; self.advance()
        pointers = 0
        while self.current_token.type == TT_ASTERISK: pointers += 1; self.advance()
        return full_type + '*' * pointers

    def top_level_declaration(self):
        full_type = self.get_type_string()
        if self.current_token.type != TT_ID: raise Exception("変数名または関数名が必要です。")
        if self.tokens[self.token_idx + 1].type == TT_LPAREN:
            func_name = self.current_token.value; self.advance(); return self.function_definition_continue(full_type, func_name)
        else:
            var_name = self.current_token.value; self.advance(); return self.var_declaration_continue(full_type, var_name)

    def function_definition_continue(self, return_type, func_name):
        self.advance(); params = self.parse_params(); self.advance(); self.advance(); body = self.block_statement()
        return FunctionDefNode(return_type, func_name, params, body)

    def var_declaration_continue(self, var_type, var_name):
        self.advance(); return VarDeclNode(var_type, var_name)

    def parse_params(self):
        params = []
        while self.current_token.type != TT_RPAREN:
            if self.current_token.type != TT_KEYWORD or self.current_token.value not in ('int', 'char'): raise Exception("引数の型は 'int' または 'char' のみ対応しています。")
            self.advance()
            while self.current_token.type == TT_ASTERISK: self.advance()
            if self.current_token.type != TT_ID: raise Exception("引数名 (ID) が必要です。")
            params.append(self.current_token); self.advance()
            if self.current_token.type == TT_COMMA: self.advance()
            elif self.current_token.type != TT_RPAREN: raise Exception("引数リストの区切りは ',' または ')' である必要があります。")
        return params

    def block_statement(self):
        body = []
        while self.current_token.type != TT_RBRACE and self.current_token.type != TT_EOF: body.append(self.statement())
        self.advance(); return body

    def statement(self):
        if self.current_token.type == TT_KEYWORD and (self.current_token.value in ('int', 'void', 'char')):
            full_type = self.get_type_string(); var_name = self.current_token.value; self.advance(); self.advance(); return VarDeclNode(full_type, var_name)
        
        if self.current_token.type == TT_ID or self.current_token.type == TT_ASTERISK or self.current_token.type == TT_AMPERSAND:
            if self.peek_for_assignment(): return self.assignment()

        if self.current_token.type == TT_KEYWORD:
            val = self.current_token.value
            if val == 'print': self.advance(); self.advance(); expr = self.expression(); self.advance(); self.advance(); return PrintNode(expr)
            if val == 'debug': self.advance(); self.advance(); return DebugNode()
            if val == 'if': return self.if_statement()
            if val == 'return': self.advance(); expr = self.expression(); self.advance(); return ReturnNode(expr)

        if self.current_token.type == TT_ID and self.peek().type == TT_LPAREN: return self.function_call()
        raise Exception(f"文エラー: {self.current_token}")

    def peek_for_assignment(self):
        i = self.token_idx
        while self.tokens[i].type == TT_ASTERISK: i += 1
        if self.tokens[i].type == TT_ID and self.tokens[i+1].type == TT_ASSIGN: return True
        return False

    def assignment(self):
        target = self.factor()
        if self.current_token.type != TT_ASSIGN: raise Exception("代入演算子 '=' が必要です。")
        self.advance(); expr = self.expression(); self.advance(); return AssignmentNode(target, expr)

    def if_statement(self):
        self.advance(); self.advance(); condition = self.expression(); self.advance(); self.advance(); true_body = self.block_statement()
        false_body = None
        if self.current_token.type == TT_KEYWORD and self.current_token.value == 'else': self.advance(); self.advance(); false_body = self.block_statement()
        return IfNode(condition, true_body, false_body)

    def parse_arguments(self):
        args = []
        if self.current_token.type == TT_RPAREN: return args
        args.append(self.expression())
        while self.current_token.type == TT_COMMA: self.advance(); args.append(self.expression())
        return args

    def function_call(self):
        func_name = self.current_token.value; self.advance(); self.advance(); args = self.parse_arguments(); self.advance()
        if self.current_token.type == TT_SEMICOLON: self.advance()
        return FunctionCallNode(func_name, args)

    def expression(self): return self.comparison()
    def comparison(self):
        node = self.arithmetic()
        while self.current_token.type in (TT_EQ, TT_NEQ, TT_LT, TT_GT):
            op_tok = self.current_token; self.advance(); right = self.arithmetic()
            node = BinaryOpNode(node, op_tok, right)
        return node
    def arithmetic(self): return self.bin_op(self.term, [TT_PLUS, TT_MINUS])
    def term(self): return self.bin_op(self.factor, [TT_MUL, TT_DIV])
    def factor(self):
        tok = self.current_token
        if tok.type == TT_AMPERSAND or tok.type == TT_ASTERISK:
            self.advance(); operand = self.factor(); return UnaryOpNode(tok, operand)
        elif tok.type == TT_INT: self.advance(); return NumberNode(tok.value)
        elif tok.type == TT_STRING: self.advance(); return StringNode(tok.value)
        elif tok.type == TT_ID:
            if self.peek().type == TT_LPAREN: return self.function_call()
            self.advance(); return VarAccessNode(tok.value)
        elif tok.type == TT_LPAREN: self.advance(); expr = self.expression(); self.advance(); return expr
        raise Exception(f"式エラー: {tok}")
    def bin_op(self, func, ops):
        left = func()
        while self.current_token.type in ops:
            op_tok = self.current_token; self.advance(); right = func()
            left = BinaryOpNode(left, op_tok, right)
        return left

# ==========================================
# 5. 実行環境 (Interpreter)
# ==========================================

class Interpreter:
    def __init__(self, output_callback, debug_callback):
        self.memory = Memory()
        self.functions = {}
        self.global_symtable = {}
        self.string_literals = {}
        self.call_stack = []
        self.output_callback = output_callback
        self.debug_callback = debug_callback 

    def log(self, message): self.output_callback(str(message) + "\n")
    def visit(self, node):
        method_name = f'visit_{type(node).__name__}'
        method = getattr(self, method_name, self.no_visit_method)
        return method(node)
    def no_visit_method(self, node): raise Exception(f"No visit_{type(node).__name__}")

    def _get_size_by_type(self, var_type):
        # ポインタ型はアドレスサイズ (4バイト)
        if var_type.endswith('*'): return INT_SIZE 
        # 基本型
        if var_type == 'int': return INT_SIZE
        if var_type == 'char': return CHAR_SIZE
        if var_type == 'void': return 0
        raise Exception(f"不明な型サイズ: {var_type}")

    def _store_string_literal(self, string_value):
        if string_value not in self.string_literals:
            size = len(string_value) + 1 
            addr = self.memory.allocate(size_in_bytes=size) 
            
            for i, char in enumerate(string_value):
                self.memory.set_byte_value(addr + i, ord(char))
            
            self.memory.set_byte_value(addr + size - 1, 0)
            self.string_literals[string_value] = addr
        
        return self.string_literals[string_value]

    def _read_string_from_memory(self, address):
        if address < 1000: return f"Error: Invalid string address {address}"
        
        result = []
        current_addr = address
        
        while True:
            try:
                byte_value = self.memory.get_byte_value(current_addr)
                if byte_value == 0:
                    break
                result.append(chr(byte_value))
                current_addr += 1
            except Exception:
                break
        return "".join(result)

    def _find_and_store_string_literals_recursive(self, node):
        """ASTを再帰的に走査し、StringNodeを発見したらメモリに配置する"""
        if isinstance(node, list):
            for item in node:
                self._find_and_store_string_literals_recursive(item)
            return
        
        if isinstance(node, StringNode):
            self._store_string_literal(node.value)
            return

        if hasattr(node, '__dict__'):
            for key, value in node.__dict__.items():
                if isinstance(value, (ASTNode, list)):
                    self._find_and_store_string_literals_recursive(value)

    def _static_analysis_and_allocation(self, node):
        """Phase 1: ASTを走査し、静的領域と文字列リテラルのアドレスを確保"""
        for child in node.nodes:
            if isinstance(child, FunctionDefNode): 
                self.functions[child.name] = child
                # 関数内の文字列リテラルをここで確保 (再帰走査)
                self._find_and_store_string_literals_recursive(child) 
            elif isinstance(child, VarDeclNode): 
                self._declare_variable(child.name, child.var_type, is_global=True)
            elif isinstance(child, AssignmentNode) and isinstance(child.expr, StringNode):
                # トップレベルの代入（初期化子代わりの使用を想定）の文字列リテラルを配置
                self._store_string_literal(child.expr.value) 

    def visit_ProgramNode(self, node):
        # --- PHASE 1: 静的解析と確保 ---
        self._static_analysis_and_allocation(node)

        # --- PHASE 2: 実行 ---
        if 'main' in self.functions:
            self.log("--- Executing main ---")
            try: self.call_function('main', [])
            except ReturnSignal as e: self.log(f"Warning: main関数から値 {e.value} が返されましたが無視されました。")
            self.log("--- Finished ---")
        else: self.log("Error: main関数がありません")

    def _declare_variable(self, name, var_type, is_global=False):
        addr = self.memory.allocate(size_in_bytes=self._get_size_by_type(var_type))
        symbol = Symbol(var_type, addr)
        if is_global: self.global_symtable[name] = symbol
        else: self.call_stack[-1][name] = symbol
        self.memory.set_int_value(addr, 0)

    def _get_symbol(self, name):
        if self.call_stack and name in self.call_stack[-1]: return self.call_stack[-1][name]
        if name in self.global_symtable: return self.global_symtable[name]
        raise Exception(f"未定義変数: {name}")

    def call_function(self, name, arg_values):
        func_node = self.functions.get(name)
        if not func_node: raise Exception(f"未定義関数: {name}")

        new_symtable = {}
        if len(arg_values) != len(func_node.params): raise Exception(f"関数 '{name}' の引数の数が一致しません")
            
        for param_node, arg_value in zip(func_node.params, arg_values):
            param_name = param_node.value
            addr = self.memory.allocate(size_in_bytes=INT_SIZE)
            new_symtable[param_name] = Symbol('int', addr) 
            self.memory.set_int_value(addr, arg_value) 

        self.call_stack.append(new_symtable)
        
        try:
            for stmt in func_node.body: self.visit(stmt)
        except ReturnSignal as ret:
            self.call_stack.pop(); return ret.value
        
        self.call_stack.pop()
        return None if func_node.return_type == 'void' else 0

    def visit_FunctionCallNode(self, node):
        arg_values = [self.visit(arg_expr) for arg_expr in node.args]
        return self.call_function(node.name, arg_values)

    def visit_VarDeclNode(self, node): self._declare_variable(node.name, node.var_type)

    def visit_AssignmentNode(self, node):
        r_value = self.visit(node.expr)

        if isinstance(node.target, VarAccessNode):
            symbol = self._get_symbol(node.target.name)
            self.memory.set_int_value(symbol.address, r_value)
        elif isinstance(node.target, UnaryOpNode) and node.target.op.type == TT_ASTERISK:
            target_address = self.visit(node.target.operand) 
            self.memory.set_int_value(target_address, r_value)
        else:
            raise Exception("代入の左辺値が不正です。")

    def visit_VarAccessNode(self, node):
        symbol = self._get_symbol(node.name)
        return self.memory.get_int_value(symbol.address)
    
    def visit_UnaryOpNode(self, node):
        if node.op.type == TT_AMPERSAND:
            if not isinstance(node.operand, VarAccessNode): raise Exception("& 演算子は変数にのみ適用可能です。")
            symbol = self._get_symbol(node.operand.name)
            return symbol.address
        elif node.op.type == TT_ASTERISK:
            address_to_dereference = self.visit(node.operand) 
            return self.memory.get_int_value(address_to_dereference)
        raise Exception(f"不明な単項演算子: {node.op.type}")

    def visit_NumberNode(self, node): return node.value
    
    def visit_StringNode(self, node):
        return self.string_literals[node.value]
    
    def visit_DebugNode(self, node):
        self.log(">>> Breakpoint <<<")
        self.debug_callback(self.global_symtable, self.call_stack[-1] if self.call_stack else {})
        
    def visit_PrintNode(self, node):
        output_value = self.visit(node.expr)

        is_string_output = isinstance(node.expr, StringNode)
        if isinstance(node.expr, VarAccessNode):
            symbol = self._get_symbol(node.expr.name)
            if symbol.type.startswith('char*'):
                is_string_output = True

        if is_string_output:
            string_output = self._read_string_from_memory(output_value)
            self.log(f"[STRING OUTPUT] {string_output}")
        else:
            self.log(f"[INT OUTPUT] {output_value}")
            
    def visit_ReturnNode(self, node):
        raise ReturnSignal(self.visit(node.expr))
    
    def visit_BinaryOpNode(self, node):
        left = self.visit(node.left); right = self.visit(node.right)
        if node.op.type == TT_PLUS: return left + right
        elif node.op.type == TT_MINUS: return left - right
        elif node.op.type == TT_MUL: return left * right
        elif node.op.type == TT_DIV: return int(left / right)
        elif node.op.type == TT_EQ: return 1 if left == right else 0
        elif node.op.type == TT_NEQ: return 1 if left != right else 0
        elif node.op.type == TT_LT: return 1 if left < right else 0
        elif node.op.type == TT_GT: return 1 if left > right else 0
    def visit_IfNode(self, node):
        if self.visit(node.condition) != 0:
            for stmt in node.true_body: self.visit(stmt)
        elif node.false_body:
            for stmt in node.false_body: self.visit(stmt)

# ==========================================
# 6. GUI アプリケーション
# ==========================================

class CInterpreterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Cインタープリタ (メモリ統合・最終版)")
        self.root.geometry("600x750")
        tk.Label(root, text="Cソースコード:", font=("Arial", 10, "bold")).pack(anchor="w", padx=10)
        self.input_area = scrolledtext.ScrolledText(root, height=20, font=("Consolas", 11))
        self.input_area.pack(padx=10, pady=5, fill="both", expand=True)

        default_code = """int val;
char *msg; 
char *msg2;

void main() {
    val = 123;
    msg = "Hello World!"; 
    msg2 = "Test";
    
    print(val);
    print(msg); 
    print(msg2);
    
    debug; // メモリビュアーでアドレスを確認
}
"""
        self.input_area.insert(tk.END, default_code)
        
        button_frame = tk.Frame(root)
        button_frame.pack(pady=10)
        
        self.run_button = tk.Button(button_frame, text="実行 (Run)", command=self.run_code, bg="#4CAF50", fg="white", font=("Arial", 12, "bold"))
        self.run_button.pack(side=tk.LEFT, padx=10)
        
        self.memory_button = tk.Button(button_frame, text="メモリビューア表示", command=self.show_memory_viewer, bg="#F0AD4E", fg="white", font=("Arial", 10))
        self.memory_button.pack(side=tk.LEFT, padx=10)

        self.output_area = scrolledtext.ScrolledText(root, height=10, font=("Consolas", 10), bg="#f0f0f0")
        self.output_area.pack(padx=10, pady=5, fill="both", expand=True)

    def write_output(self, text):
        self.output_area.insert(tk.END, text)
        self.output_area.see(tk.END)
        
    def show_breakpoint_popup(self, global_symtable, local_symtable):
        popup = Toplevel(self.root)
        popup.title("Breakpoint"); popup.geometry("400x450"); popup.grab_set()
        tk.Label(popup, text="⏸ 一時停止中", font=("Arial", 14), fg="red").pack(pady=10)
        info_text = scrolledtext.ScrolledText(popup, height=14)
        info_text.pack(padx=10, pady=5, fill="both")
        
        current_interpreter = self.root.interpreter_instance
        
        def display_symbols(title, symtable, interpreter):
            info_text.insert(tk.END, f"--- {title} ---\n")
            for name, symbol in symtable.items():
                try:
                    value = interpreter.memory.get_int_value(symbol.address)
                    
                    line = f"{symbol.type} {name} (Addr: {symbol.address}, Size: {interpreter._get_size_by_type(symbol.type)}B) = {value}"
                    
                    if symbol.type.startswith('char*'):
                        target_string = interpreter._read_string_from_memory(value)
                        line += f" -> [Points to Addr {value}. Content: \"{target_string[:15]}...\"]"
                        
                    info_text.insert(tk.END, line + "\n")
                except:
                    info_text.insert(tk.END, f"{symbol.type} {name} (Addr: {symbol.address}) = [Error: Access/Type Mismatch]\n")
        
        display_symbols("Global Symbols", global_symtable, current_interpreter)
        display_symbols("Local Symbols", local_symtable, current_interpreter)

        button_frame = tk.Frame(popup)
        button_frame.pack(pady=10)
        
        tk.Button(button_frame, text="メモリビューア起動", command=self.show_memory_viewer, bg="#F0AD4E", fg="white", font=("Arial", 10)).pack(side=tk.LEFT, padx=10)
        tk.Button(button_frame, text="再開", command=popup.destroy, bg="#2196F3", fg="white", font=("Arial", 12)).pack(side=tk.LEFT, padx=10)

        self.root.wait_window(popup)

    def show_memory_viewer(self):
        if not hasattr(self.root, 'interpreter_instance'):
            self.write_output("\n[WARNING] コードを実行してからメモリビューアを開いてください。\n")
            return

        interpreter = self.root.interpreter_instance
        memory_data = interpreter.memory.heap
        
        viewer = Toplevel(self.root)
        viewer.title(f"Memory Viewer (Heap) - {INT_SIZE} Bytes/int")
        viewer.geometry("500x500")
        
        tk.Label(viewer, text="仮想メモリ内容 (アドレス: バイト値 [10進数] (16進数))", font=("Arial", 10, "bold")).pack(pady=10)
        
        memory_text = scrolledtext.ScrolledText(viewer, height=20, font=("Consolas", 10), bg="#f5f5f5")
        memory_text.pack(padx=10, pady=5, fill="both", expand=True)
        
        sorted_addresses = sorted(memory_data.keys())
        
        if not sorted_addresses:
            memory_text.insert(tk.END, "メモリはまだ割り当てられていません。")
            return

        memory_text.insert(tk.END, f"Next Free Address: {interpreter.memory.next_free_address}\n")
        memory_text.insert(tk.END, "="*50 + "\n")

        output_lines = []
        for addr in sorted_addresses:
            byte_value = memory_data[addr]
            
            hex_value = f"0x{byte_value:02x}"
            char_repr = repr(chr(byte_value)) if 32 <= byte_value <= 126 else '..'
            
            line = f"[{addr:04d}]: {byte_value:03d} ({hex_value}) | Char: {char_repr}"
            
            if (addr - 1000) % INT_SIZE == 0:
                 line = f"--- INT START --- " + line

            output_lines.append(line)

        memory_text.insert(tk.END, "\n".join(output_lines))
        memory_text.config(state=tk.DISABLED)

    def run_code(self):
        self.output_area.delete('1.0', tk.END)
        code = self.input_area.get("1.0", tk.END)
        if not code.strip(): return
        
        try:
            lexer = Lexer(code)
            tokens = lexer.make_tokens()
            parser = Parser(tokens)
            ast = parser.parse()
            
            interpreter = Interpreter(self.write_output, self.show_breakpoint_popup)
            self.root.interpreter_instance = interpreter
            
            interpreter.visit(ast)
            
        except Exception as e:
            self.output_area.insert(tk.END, f"\n[ERROR] {e}\n")

if __name__ == "__main__":
    root = tk.Tk()
    app = CInterpreterGUI(root)
    root.mainloop()