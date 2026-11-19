import "@mantine/dates/styles.css";
import { Button, Group, Input, Text } from "@mantine/core";
import { RichDescription } from "@rjsf/core";
import type CoreForm from "@rjsf/core";
import { Form } from "@rjsf/mantine";
import {
  type DescriptionFieldProps,
  type IconButtonProps,
  type RJSFSchema,
  type TitleFieldProps,
  type UiSchema,
  type WidgetProps,
  TranslatableString,
  englishStringTranslator,
  replaceStringParameters,
} from "@rjsf/utils";
import { customizeValidator } from "@rjsf/validator-ajv8";
import localizeZh from "ajv-i18n/localize/zh";
import { type Ref, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { JsonSchema } from "../api";
import { PathPicker } from "./PathPicker";

// 插件的 Settings 按完整 JSON Schema 渲染（列表、嵌套对象、$ref/$defs、枚举、约束都由 rjsf 处理）。
// 插件在字段上用 json_schema_extra 写 `ui:` 开头的键，这里抽出来拼成 uiSchema；公开承诺的只有
// ui:widget（textarea、file-path）、ui:placeholder、ui:help、ui:order，其他 ui: 键原样交给 rjsf。
// `format: "file-path"` 的字符串和 `ui:widget: "file-path"` 都用带「浏览」的路径框。

const ZH: Record<TranslatableString, string> = {
  [TranslatableString.ArrayItemTitle]: "项目",
  [TranslatableString.MissingItems]: "缺少列表项的定义",
  [TranslatableString.EmptyArray]: "还没有内容，用下面的「添加」加一项。",
  [TranslatableString.YesLabel]: "是",
  [TranslatableString.NoLabel]: "否",
  [TranslatableString.CloseLabel]: "关闭",
  [TranslatableString.ErrorsLabel]: "有错误",
  [TranslatableString.NewStringDefault]: "新值",
  [TranslatableString.AddButton]: "添加",
  [TranslatableString.AddItemButton]: "添加",
  [TranslatableString.CopyButton]: "复制",
  [TranslatableString.ExpandButton]: "展开",
  [TranslatableString.MoveDownButton]: "下移",
  [TranslatableString.MoveUpButton]: "上移",
  [TranslatableString.RemoveButton]: "删除",
  [TranslatableString.NowLabel]: "现在",
  [TranslatableString.ClearLabel]: "清除",
  [TranslatableString.AriaDateLabel]: "选择日期",
  [TranslatableString.PreviewLabel]: "预览",
  [TranslatableString.DecrementAriaLabel]: "减 1",
  [TranslatableString.IncrementAriaLabel]: "加 1",
  [TranslatableString.OptionalObjectAdd]: "填写这一项",
  [TranslatableString.OptionalObjectRemove]: "清空这一项",
  [TranslatableString.OptionalObjectEmptyMsg]: "这一项没有填写",
  [TranslatableString.Type]: "类型",
  [TranslatableString.Value]: "值",
  [TranslatableString.ClearButton]: "清空",
  [TranslatableString.UnknownFieldType]: "不认识的字段类型 %1",
  [TranslatableString.OptionPrefix]: "选项 %1",
  [TranslatableString.TitleOptionPrefix]: "%1 选项 %2",
  [TranslatableString.KeyLabel]: "%1 的键",
  [TranslatableString.DeprecatedLabel]: "%1（已弃用）",
  [TranslatableString.CycleDetected]: "字段「%1」的 $ref 循环引用，可以再展开一层",
  [TranslatableString.InvalidObjectField]: "对象字段「%1」的配置不正确：_%2_。",
  [TranslatableString.UnsupportedField]: "不支持这种字段结构。",
  [TranslatableString.UnsupportedFieldWithId]: "字段 `%1` 的结构不支持。",
  [TranslatableString.UnsupportedFieldWithReason]: "不支持这种字段结构：_%1_。",
  [TranslatableString.UnsupportedFieldWithIdAndReason]: "字段 `%1` 的结构不支持：_%2_。",
  [TranslatableString.FilesInfo]: "**%1**（%2，%3 字节）",
};

function translateString(text: TranslatableString, params?: string[]): string {
  const zh = ZH[text];
  return zh === undefined ? englishStringTranslator(text, params) : replaceStringParameters(zh, params);
}

const validator = customizeValidator({ customFormats: { "file-path": () => true } }, localizeZh);

type Node = Record<string, unknown>;

function isNode(value: unknown): value is Node {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 去掉一个节点里的 ui: 键，返回去掉后的节点和它（含子节点）的 uiSchema。
// $ref 指向的 $defs 里的 ui: 键并到引用处，引用处自己写的优先。
function splitNode(node: unknown, defsUi: Record<string, Node>): { schema: unknown; ui: Node } {
  if (Array.isArray(node)) {
    return { schema: node.map((item) => splitNode(item, defsUi).schema), ui: {} };
  }
  if (!isNode(node)) {
    return { schema: node, ui: {} };
  }
  const schema: Node = {};
  let ui: Node = {};
  const ref = node.$ref;
  if (typeof ref === "string" && ref.startsWith("#/$defs/")) {
    ui = { ...defsUi[ref.slice("#/$defs/".length)] };
  }
  for (const [key, value] of Object.entries(node)) {
    if (key.startsWith("ui:")) {
      ui[key] = value;
      continue;
    }
    if (key === "properties" && isNode(value)) {
      const properties: Node = {};
      for (const [name, child] of Object.entries(value)) {
        const split = splitNode(child, defsUi);
        properties[name] = split.schema;
        if (Object.keys(split.ui).length > 0) {
          ui[name] = { ...(isNode(ui[name]) ? ui[name] : {}), ...split.ui };
        }
      }
      schema[key] = properties;
      continue;
    }
    if ((key === "items" || key === "additionalProperties") && isNode(value)) {
      const split = splitNode(value, defsUi);
      schema[key] = split.schema;
      if (Object.keys(split.ui).length > 0) {
        ui[key] = split.ui;
      }
      continue;
    }
    if ((key === "anyOf" || key === "oneOf" || key === "allOf") && Array.isArray(value)) {
      schema[key] = value.map((branch) => {
        const split = splitNode(branch, defsUi);
        ui = { ...split.ui, ...ui };
        return split.schema;
      });
      continue;
    }
    if (key === "$defs") {
      continue;
    }
    schema[key] = splitNode(value, defsUi).schema;
  }
  return { schema, ui };
}

export function splitUiSchema(source: JsonSchema): { schema: RJSFSchema; uiSchema: UiSchema } {
  const root = source as Node;
  const defs = isNode(root.$defs) ? root.$defs : {};
  // 先抽 $defs 自己的 ui:。$defs 之间互相引用时，只有排在前面的定义的 ui: 能并到后面的引用处。
  const defsUi: Record<string, Node> = {};
  const cleanDefs: Node = {};
  for (const [name, def] of Object.entries(defs)) {
    const split = splitNode(def, defsUi);
    defsUi[name] = split.ui;
    // Pydantic 默认拿类名当标题（和 $defs 的键相同），列表项会显示成「Rule」；去掉后显示「规则-1」。
    const cleanedDef = split.schema as Node;
    if (cleanedDef.title === name) {
      delete cleanedDef.title;
    }
    cleanDefs[name] = cleanedDef;
  }
  const { schema, ui } = splitNode(root, defsUi);
  const cleaned = schema as Node;
  // 卡片已经有标题和说明；根上 Pydantic 写的类名和类的 docstring 不显示。
  delete cleaned.title;
  delete cleaned.description;
  if (Object.keys(cleanDefs).length > 0) {
    cleaned.$defs = cleanDefs;
  }
  return { schema: cleaned as RJSFSchema, uiSchema: ui as UiSchema };
}

function FilePathWidget({
  id,
  label,
  hideLabel,
  value,
  required,
  disabled,
  readonly,
  placeholder,
  rawErrors,
  schema,
  options,
  onChange,
}: WidgetProps) {
  const description = hideLabel ? undefined : (options.description ?? schema.description);
  const text = typeof value === "string" ? value : "";
  return (
    <Input.Wrapper
      id={id}
      label={hideLabel ? undefined : label}
      required={required}
      description={description}
      error={rawErrors && rawErrors.length > 0 ? rawErrors.join("\n") : undefined}
    >
      <Group gap="xs" wrap="nowrap" mt={4}>
        {disabled || readonly ? (
          <Input id={id} value={text} readOnly style={{ flex: 1 }} ff="monospace" aria-label={label} />
        ) : (
          <PathPicker
            id={id}
            label={label}
            mode="file"
            placeholder={placeholder}
            value={text}
            onChange={(next) => onChange(next === "" ? options.emptyValue : next)}
          />
        )}
      </Group>
    </Input.Wrapper>
  );
}

// 主题默认用正文字号显示说明，和输入框自带的说明一样改成小字。
function DescriptionField({ id, description, registry, uiSchema }: DescriptionFieldProps) {
  if (!description) {
    return null;
  }
  return (
    <Text id={id} size="xs" c="dimmed" mb={4}>
      <RichDescription description={description} registry={registry} uiSchema={uiSchema} />
    </Text>
  );
}

// 主题用三级标题显示对象标题；列表项的标题是 rjsf 拼的「外框标题-序号」，和外框重复。
// 列表项（id 形如 root_rules_0__title）改成小号的「第 N 条」，其余对象标题也改小一号。
const ARRAY_ITEM_TITLE = /_(\d+)__title$/;

function TitleField({ id, title, optionalDataControl }: TitleFieldProps) {
  const item = ARRAY_ITEM_TITLE.exec(id);
  const text = item ? `第 ${Number(item[1]) + 1} 条` : title;
  if (!text) {
    return null;
  }
  const heading = (
    <Text id={id} size="sm" fw={600} c={item ? "dimmed" : undefined} mb={4}>
      {text}
    </Text>
  );
  if (!optionalDataControl) {
    return heading;
  }
  return (
    <Group justify="space-between" wrap="nowrap">
      {heading}
      {optionalDataControl}
    </Group>
  );
}

function AddButton({ onClick, disabled, id, className }: IconButtonProps) {
  return (
    <Button
      id={id}
      className={className}
      variant="light"
      size="xs"
      disabled={disabled}
      onClick={onClick}
    >
      + 添加
    </Button>
  );
}

const widgets = { "file-path": FilePathWidget };
const templates = {
  ButtonTemplates: { AddButton },
  DescriptionFieldTemplate: DescriptionField,
  TitleFieldTemplate: TitleField,
};

export interface PluginSettingsFormHandle {
  // 校验当前值并把错误显示在字段下面；有错误时返回 false。
  validate: () => boolean;
}

export default function PluginSettingsForm({
  schema,
  value,
  onChange,
  disabled = false,
  ref,
}: {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
  ref?: Ref<PluginSettingsFormHandle>;
}) {
  const form = useRef<CoreForm>(null);
  const split = useMemo(() => splitUiSchema(schema), [schema]);
  const uiSchema = useMemo<UiSchema>(
    () => ({ ...split.uiSchema, "ui:submitButtonOptions": { norender: true } }),
    [split.uiSchema],
  );
  // 只在点保存时校验，免得刚点「+ 添加」还没填完就满屏必填提示；保存失败过之后边改边校验。
  const [checked, setChecked] = useState(false);
  useImperativeHandle(
    ref,
    () => ({
      validate: () => {
        setChecked(true);
        return form.current?.validateForm() ?? true;
      },
    }),
    [],
  );

  return (
    <Form
      ref={form}
      schema={split.schema}
      uiSchema={uiSchema}
      formData={value}
      validator={validator}
      widgets={widgets}
      templates={templates}
      translateString={translateString}
      disabled={disabled}
      liveValidate={checked ? "onChange" : false}
      showErrorList={false}
      noHtml5Validate
      onChange={(event) => onChange((event.formData ?? {}) as Record<string, unknown>)}
      onSubmit={() => undefined}
    />
  );
}
