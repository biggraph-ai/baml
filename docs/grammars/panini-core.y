/*
 * Panini language syntax prototype.
 *
 * This is an action-free GNU Bison grammar used to verify that the proposed
 * declaration and orchestration syntax is LALR(1). It is not the production
 * language grammar: templates and provider configuration bodies are emitted by
 * a mode-aware lexer as INSTRUCTION_LITERAL and PROVIDER_BLOCK tokens.
 *
 * Verify and emit state, Graphviz, and XML debug reports with:
 *   tools/check-panini-grammar.sh
 */

%define api.pure full
%define parse.error detailed
%locations

%token IDENT STRING_LITERAL INSTRUCTION_LITERAL PROVIDER_BLOCK
%token INTEGER_LITERAL FLOAT_LITERAL TRUE FALSE NULL_LITERAL

%token PACKAGE IMPORT AS
%token CLASS AGENT INTERFACE ENUM TYPE
%token EXTENDS IMPLEMENTS REQUIRES
%token PUBLIC PRIVATE PROTECTED INTERNAL STATIC ABSTRACT FINAL OVERRIDE ASYNC
%token PROVIDER INSTRUCTION CAPABILITIES THROWS
%token DECISION_MODEL DECIDE OVER ABSTAIN_BELOW WRITES
%token GRAPH STATE NODE START END POLICY RULE
%token ENTRY EXIT INVARIANT WHEN OTHERWISE MAX_VISITS TIMEOUT
%token PARALLEL BRANCH JOIN MERGE USING COMPENSATE WITH
%token LET CONST RETURN THROW DEFER IF ELSE WHILE FOR IN MATCH CATCH SPAWN AWAIT
%token BREAK CONTINUE NEW

%token ARROW FAT_ARROW
%token EQ PLUS_EQ MINUS_EQ STAR_EQ SLASH_EQ PERCENT_EQ
%token OR_OR AND_AND PIPE CARET AMP
%token EQ_EQ NOT_EQ LT GT LT_EQ GT_EQ SHIFT_LEFT SHIFT_RIGHT
%token PLUS MINUS STAR SLASH PERCENT BANG TILDE QUESTION COALESCE
%token AT DOUBLE_AT DOT ELLIPSIS

%right EQ PLUS_EQ MINUS_EQ STAR_EQ SLASH_EQ PERCENT_EQ
%left COALESCE
%left OR_OR
%left AND_AND
%left PIPE
%left CARET
%left AMP
%left EQ_EQ NOT_EQ
%left LT GT LT_EQ GT_EQ
%left SHIFT_LEFT SHIFT_RIGHT
%left PLUS MINUS
%left STAR SLASH PERCENT
%precedence BANG TILDE UMINUS AWAIT

%start compilation_unit

%%

compilation_unit
    : package_opt import_decl_list declaration_list
    ;

package_opt
    : %empty
    | PACKAGE qualified_name ';'
    ;

import_decl_list
    : %empty
    | import_decl_list IMPORT qualified_name import_alias_opt ';'
    ;

import_alias_opt
    : %empty
    | AS IDENT
    ;

declaration_list
    : %empty
    | declaration_list declaration
    ;

declaration
    : attribute_list enum_decl
    | attribute_list class_decl
    | attribute_list agent_decl
    | attribute_list interface_decl
    | type_alias_decl
    | provider_decl
    | graph_decl
    | policy_decl
    ;

attribute_list
    : %empty
    | attribute_list attribute
    ;

attribute
    : AT qualified_name attribute_args_opt
    | DOUBLE_AT qualified_name attribute_args_opt
    ;

attribute_args_opt
    : %empty
    | '(' argument_list_opt ')'
    ;

modifier_list
    : %empty
    | modifier_list modifier
    ;

modifier
    : PUBLIC | PRIVATE | PROTECTED | INTERNAL | STATIC
    | ABSTRACT | FINAL | OVERRIDE | ASYNC
    ;

enum_decl
    : modifier_list ENUM IDENT '{' enum_member_list_opt '}'
    ;

enum_member_list_opt
    : %empty
    | enum_member_list
    ;

enum_member_list
    : enum_member
    | enum_member_list ',' enum_member
    | enum_member_list ','
    ;

enum_member
    : attribute_list IDENT
    ;

class_decl
    : modifier_list CLASS IDENT generic_params_opt class_bases_opt class_body
    ;

agent_decl
    : modifier_list AGENT IDENT generic_params_opt class_bases_opt class_body
    ;

class_bases_opt
    : %empty
    | EXTENDS type_ref implements_opt
    | IMPLEMENTS type_ref_list
    ;

implements_opt
    : %empty
    | IMPLEMENTS type_ref_list
    ;

class_body
    : '{' class_member_list '}'
    ;

class_member_list
    : %empty
    | class_member_list attribute_list class_member
    ;

class_member
    : java_style_member_decl
    | system_instruction_decl
    ;

initializer_opt
    : %empty
    | EQ expression
    ;

interface_decl
    : modifier_list INTERFACE IDENT generic_params_opt requires_opt interface_body
    ;

requires_opt
    : %empty
    | REQUIRES type_ref_list
    ;

interface_body
    : '{' interface_member_list '}'
    ;

interface_member_list
    : %empty
    | interface_member_list attribute_list interface_member
    ;

interface_member
    : java_style_method_signature ';'
    | java_style_member_decl
    | associated_type_decl
    ;

associated_type_decl
    : TYPE IDENT type_bound_opt type_default_opt ';'
    ;

type_bound_opt
    : %empty
    | EXTENDS type_bound_list
    ;

type_bound_list
    : type_ref
    | type_bound_list AMP type_ref
    ;

type_default_opt
    : %empty
    | EQ type_ref
    ;

java_style_member_decl
    : modifier_list type_ref IDENT java_style_member_tail
    ;

java_style_member_tail
    : initializer_opt ';'
    | generic_params_opt parameter_list throws_opt function_body
    ;

java_style_method_signature
    : modifier_list type_ref IDENT generic_params_opt parameter_list throws_opt
    ;

throws_opt
    : %empty
    | THROWS type_ref
    ;

function_body
    : block
    | inference_body
    | decision_body
    ;

decision_body
    : '{' DECISION_MODEL expression ';' DECIDE expression OVER '{' decision_option_list '}' decision_gate_opt ';' '}'
    ;

decision_option_list
    : IDENT ':' STRING_LITERAL ';'
    | decision_option_list IDENT ':' STRING_LITERAL ';'
    ;

decision_gate_opt
    : %empty
    | ABSTAIN_BELOW expression
    ;

inference_body
    : '{' PROVIDER expression ';' capabilities_opt INSTRUCTION INSTRUCTION_LITERAL ';' '}'
    ;

capabilities_opt
    : %empty
    | CAPABILITIES expression ';'
    ;

system_instruction_decl
    : INSTRUCTION INSTRUCTION_LITERAL ';'
    ;

parameter_list
    : '(' parameter_seq_opt ')'
    ;

parameter_seq_opt
    : %empty
    | parameter_seq
    ;

parameter_seq
    : parameter
    | parameter_seq ',' parameter
    ;

parameter
    : type_ref IDENT default_arg_opt
    ;

default_arg_opt
    : %empty
    | EQ expression
    ;

generic_params_opt
    : %empty
    | LT generic_param_seq GT
    ;

generic_param_seq
    : generic_param
    | generic_param_seq ',' generic_param
    ;

generic_param
    : IDENT type_bound_opt
    ;

type_alias_decl
    : TYPE IDENT generic_params_opt EQ type_ref ';'
    ;

provider_decl
    : PROVIDER IDENT EQ expression ';'
    | PROVIDER IDENT PROVIDER_BLOCK
    ;

policy_decl
    : POLICY IDENT policy_target_opt '{' policy_rule_list '}'
    ;

policy_target_opt
    : %empty
    | FOR IDENT
    ;

policy_rule_list
    : %empty
    | policy_rule_list policy_rule
    ;

policy_rule
    : RULE IDENT ':' policy_predicate FAT_ARROW policy_effect ';'
    | INVARIANT policy_predicate ';'
    ;

policy_predicate
    : expression
    ;

policy_effect
    : END
    | THROW expression
    | IDENT argument_list
    ;

graph_decl
    : GRAPH IDENT '(' IDENT ':' type_ref ')' graph_return_opt graph_body
    ;

graph_return_opt
    : %empty
    | ARROW type_ref
    ;

graph_body
    : '{' graph_member_list '}'
    ;

graph_member_list
    : %empty
    | graph_member_list graph_member
    ;

graph_member
    : STATE IDENT initializer_opt ';'
    | NODE IDENT USING qualified_name node_contract_list_opt ';'
    | START ARROW IDENT ';'
    | IDENT ARROW graph_transition ';'
    | ENTRY IDENT USING qualified_name ';'
    | EXIT IDENT USING qualified_name ';'
    | INVARIANT expression ';'
    | PARALLEL IDENT parallel_body
    ;

node_contract_list_opt
    : %empty
    | node_contract_list
    ;

node_contract_list
    : node_contract
    | node_contract_list node_contract
    ;

node_contract
    : MAX_VISITS INTEGER_LITERAL
    | TIMEOUT expression
    | COMPENSATE USING qualified_name
    | WRITES qualified_name
    ;

graph_transition
    : END
    | IDENT
    | WHEN expression FAT_ARROW graph_target OTHERWISE FAT_ARROW graph_target
    ;

graph_target
    : END
    | IDENT
    ;

parallel_body
    : '{' branch_decl_list join_decl '}'
    ;

branch_decl_list
    : branch_decl
    | branch_decl_list branch_decl
    ;

branch_decl
    : BRANCH IDENT USING qualified_name ';'
    ;

join_decl
    : JOIN IDENT merge_opt ';'
    ;

merge_opt
    : %empty
    | MERGE USING qualified_name
    ;

block
    : '{' statement_list '}'
    ;

statement_list
    : %empty
    | statement_list statement
    ;

statement
    : LET IDENT type_annotation_opt EQ expression ';'
    | CONST IDENT type_annotation_opt EQ expression ';'
    | RETURN expression_opt ';'
    | THROW expression ';'
    | DEFER expression ';'
    | IF '(' expression ')' block else_opt
    | WHILE '(' expression ')' block
    | FOR '(' IDENT IN expression ')' block
    | BREAK ';'
    | CONTINUE ';'
    | expression ';'
    ;

type_annotation_opt
    : %empty
    | ':' type_ref
    ;

expression_opt
    : %empty
    | expression
    ;

else_opt
    : %empty
    | ELSE block
    | ELSE IF '(' expression ')' block else_opt
    ;

expression
    : primary_expression
    | MINUS expression %prec UMINUS
    | BANG expression
    | TILDE expression
    | AWAIT expression
    | expression EQ expression
    | expression PLUS_EQ expression
    | expression MINUS_EQ expression
    | expression STAR_EQ expression
    | expression SLASH_EQ expression
    | expression PERCENT_EQ expression
    | expression COALESCE expression
    | expression OR_OR expression
    | expression AND_AND expression
    | expression PIPE expression
    | expression CARET expression
    | expression AMP expression
    | expression EQ_EQ expression
    | expression NOT_EQ expression
    | expression LT expression
    | expression GT expression
    | expression LT_EQ expression
    | expression GT_EQ expression
    | expression SHIFT_LEFT expression
    | expression SHIFT_RIGHT expression
    | expression PLUS expression
    | expression MINUS expression
    | expression STAR expression
    | expression SLASH expression
    | expression PERCENT expression
    ;

primary_expression
    : literal
    | IDENT
    | '(' expression ')'
    | array_literal
    | object_literal
    | primary_expression argument_list
    | primary_expression DOT IDENT
    | primary_expression '[' expression ']'
    | primary_expression QUESTION DOT IDENT
    | SPAWN block
    ;

argument_list
    : '(' argument_list_opt ')'
    ;

argument_list_opt
    : %empty
    | argument_seq
    ;

argument_seq
    : expression
    | argument_seq ',' expression
    ;

array_literal
    : '[' argument_list_opt ']'
    ;

object_literal
    : NEW qualified_name '{' object_field_seq_opt '}'
    ;

object_field_seq_opt
    : %empty
    | object_field_seq
    ;

object_field_seq
    : object_field
    | object_field_seq ',' object_field
    ;

object_field
    : IDENT ':' expression
    | ELLIPSIS expression
    ;

literal
    : STRING_LITERAL | INSTRUCTION_LITERAL | INTEGER_LITERAL | FLOAT_LITERAL
    | TRUE | FALSE | NULL_LITERAL
    ;

type_ref_list
    : type_ref
    | type_ref_list ',' type_ref
    ;

type_ref
    : union_type
    ;

union_type
    : postfix_type
    | union_type PIPE postfix_type
    ;

postfix_type
    : primary_type
    | postfix_type '[' ']'
    | postfix_type QUESTION
    ;

primary_type
    : qualified_name type_args_opt
    | STRING_LITERAL
    | INTEGER_LITERAL
    ;

type_args_opt
    : %empty
    | LT type_ref_list GT
    ;

qualified_name
    : IDENT
    | qualified_name DOT IDENT
    ;

%%
