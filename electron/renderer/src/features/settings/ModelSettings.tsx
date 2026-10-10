import { KeyRound, LoaderCircle, Pencil, Plus, Trash2, X } from "lucide-react";
import { useId, useState } from "react";
import type { ConfiguredModel, SettingsView } from "../../../../shared/contracts";
import { appQueryClient } from "../../app/query-client";
import { IconButton } from "../../components/IconButton";
import { Modal } from "../../components/Modal";
import { StatusBadge } from "../../components/StatusBadge";
import { ModelSettingsForm } from "./ModelSettingsForm";
import { providerLabel } from "./model-presets";
import { clientErrorMessage, settingsKeys } from "./settings.queries";

/**
 * 设置里的「对话模型」：一张已配置模型的列表，加上一个共用的编辑弹窗。
 *
 * 这一页过去是一个「模型预设」下拉框 + 一张表单 —— 下拉框里列的是**可以配的**
 * 厂商（包括一个都没配过的内置厂商），于是「我到底配了什么」在界面上根本看不出
 * 来，而且内置厂商只能有一条配置：换成另一个模型就把它盖掉了。现在这里只有用户
 * 已经存下来的配置，一条一行，写着供应商和模型名；增删改都在这一页，点「添加」
 * 或「编辑」弹出的是同一个二级页面。
 *
 * 同一时间只有一条是「当前使用」的（应用用一个模型回答），所以列表要标出来，并且
 * 允许切换。删除当前使用的那条时后端会挑剩下的第一条顶上，界面据此刷新。
 */
export function ModelSettings({ settings }: { settings: SettingsView }) {
  const [editing, setEditing] = useState<ConfiguredModel | null>(null);
  const [adding, setAdding] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ConfiguredModel | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogTitleId = useId();
  const confirmTitleId = useId();
  const models = settings.models;

  function applyView(saved: SettingsView) {
    appQueryClient.setQueryData(settingsKeys.root, saved);
  }

  async function activate(model: ConfiguredModel) {
    if (busyId !== null) return;
    setBusyId(model.id);
    setError(null);
    try {
      const saved = await window.docmind.settings.activateModel(model.id);
      applyView(saved);
      setNotice(`已切换到「${rowTitle(model)}」`);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function remove() {
    const target = pendingDelete;
    if (target === null || busyId !== null) return;
    setBusyId(target.id);
    setError(null);
    try {
      const saved = await window.docmind.settings.deleteModel(target.id);
      applyView(saved);
      setPendingDelete(null);
      setNotice(`已删除「${rowTitle(target)}」`);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setBusyId(null);
    }
  }

  /** 保存成功后的那句反馈。新建的那条会成为当前使用，所以按 id / active 都能找到。 */
  function savedNotice(saved: SettingsView, edited: ConfiguredModel | null): string {
    const target = edited
      ? saved.models.find((item) => item.id === edited.id)
      : saved.models.find((item) => item.active);
    return target ? `已保存「${rowTitle(target)}」` : "已保存";
  }

  return (
    <div className="model-settings">
      <div className="model-toolbar">
        <p className="model-toolbar-note">
          {models.length === 0
            ? "还没有配置任何模型。"
            : `共 ${models.length} 个已配置的模型，带「当前使用」标记的那个负责回答。`}
        </p>
        <button className="button button-primary" onClick={() => setAdding(true)} type="button">
          <Plus aria-hidden="true" size={16} />
          添加模型
        </button>
      </div>

      {models.length === 0 ? (
        <p className="model-empty">
          点「添加模型」，选一家供应商，填上模型名称与 API Key。 API Key
          只保存在系统钥匙串里，其余配置存在本机。
        </p>
      ) : (
        <ul className="model-list">
          {models.map((model) => (
            <li key={model.id}>
              <div className="model-row">
                <span className="model-row-icon">
                  <KeyRound aria-hidden="true" size={17} />
                </span>
                <span className="model-row-text">
                  <span className="model-row-title">
                    <strong>{providerLabel(model.preset, model.label)}</strong>
                    <span className="model-row-model">{model.model || "未指定模型"}</span>
                  </span>
                  <span className="model-row-summary">
                    {model.baseUrl || "默认地址"}
                    {" · "}
                    {model.hasApiKey ? "已保存 API Key" : "未配置 API Key"}
                  </span>
                </span>
                <span className="model-row-meta">
                  {model.active ? (
                    <StatusBadge label="当前使用" tone="success" />
                  ) : (
                    <button
                      aria-label={`设为当前使用：${rowTitle(model)}`}
                      className="button button-secondary"
                      disabled={busyId !== null}
                      onClick={() => void activate(model)}
                      type="button"
                    >
                      {busyId === model.id ? (
                        <LoaderCircle aria-hidden="true" className="spin" size={16} />
                      ) : null}
                      设为当前
                    </button>
                  )}
                  <button
                    aria-label={`编辑 ${rowTitle(model)}`}
                    className="button button-secondary"
                    disabled={busyId !== null}
                    onClick={() => {
                      setError(null);
                      setEditing(model);
                    }}
                    type="button"
                  >
                    <Pencil aria-hidden="true" size={15} />
                    编辑
                  </button>
                  <button
                    aria-label={`删除 ${rowTitle(model)}`}
                    className="button button-danger-quiet"
                    disabled={busyId !== null}
                    onClick={() => {
                      setError(null);
                      setPendingDelete(model);
                    }}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={15} />
                    删除
                  </button>
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {notice !== null ? (
        <p className="model-notice" role="status">
          {notice}
        </p>
      ) : null}
      {error !== null ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      {adding || editing !== null ? (
        <Modal
          className="model-dialog"
          labelledBy={dialogTitleId}
          onEscape={() => {
            setAdding(false);
            setEditing(null);
          }}
        >
          <header className="model-dialog-header">
            <div className="dialog-title-row">
              <h3 id={dialogTitleId}>{editing !== null ? "编辑模型" : "添加模型"}</h3>
              <IconButton
                icon={<X aria-hidden="true" size={18} />}
                label="关闭模型配置窗口"
                onClick={() => {
                  setAdding(false);
                  setEditing(null);
                }}
                size="small"
              />
            </div>
            <p>供应商决定地址与 API Key，模型决定用哪一个；两者都会写进这一条配置。</p>
          </header>
          <div className="model-dialog-body">
            <ModelSettingsForm
              onCancel={() => {
                setAdding(false);
                setEditing(null);
              }}
              onSaved={(saved) => {
                const edited = editing;
                setAdding(false);
                setEditing(null);
                setNotice(savedNotice(saved, edited));
              }}
              profile={editing}
              settings={settings}
            />
          </div>
        </Modal>
      ) : null}

      {pendingDelete !== null ? (
        <Modal
          backdropClassName="dialog-backdrop-nested"
          className="confirm-dialog"
          labelledBy={confirmTitleId}
          onEscape={() => setPendingDelete(null)}
        >
          <div className="dialog-title-row">
            <h3 id={confirmTitleId}>确认删除模型配置</h3>
            <IconButton
              icon={<X aria-hidden="true" size={18} />}
              label="关闭确认窗口"
              onClick={() => setPendingDelete(null)}
              size="small"
            />
          </div>
          <p>
            将删除「{rowTitle(pendingDelete)}」。
            {isLastOfProvider(models, pendingDelete)
              ? "这是该供应商的最后一条配置，它已保存的 API Key 也会一并删除。"
              : "同一供应商的其他配置不受影响，API Key 会保留。"}
          </p>
          {pendingDelete.active && models.length > 1 ? (
            <p>它正在被使用；删除后会自动切换到列表里的第一条。</p>
          ) : null}
          {error !== null ? (
            <p className="error-copy" role="alert">
              {error}
            </p>
          ) : null}
          <div className="dialog-actions">
            <button
              className="button button-secondary"
              onClick={() => setPendingDelete(null)}
              type="button"
            >
              取消
            </button>
            <button
              className="button button-danger"
              disabled={busyId !== null}
              onClick={() => void remove()}
              type="button"
            >
              {busyId === pendingDelete.id ? (
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
              ) : (
                <Trash2 aria-hidden="true" size={16} />
              )}
              确认删除
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

/** 一行在提示语里的写法：供应商 + 模型。列表上也是这两样。 */
function rowTitle(model: ConfiguredModel): string {
  return `${providerLabel(model.preset, model.label)} · ${model.model || "未指定模型"}`;
}

/** 这一条是不是它那家供应商的最后一条 —— 决定删除会不会顺带删掉 API Key。 */
function isLastOfProvider(models: ConfiguredModel[], target: ConfiguredModel): boolean {
  return !models.some((item) => item.id !== target.id && item.preset === target.preset);
}
