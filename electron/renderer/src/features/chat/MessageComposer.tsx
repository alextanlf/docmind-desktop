import { Send } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useEffect, useRef } from "react";
import { IconButton } from "../../components/IconButton";

type MessageComposerProps = {
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
  value: string;
};

export function MessageComposer({ disabled, onChange, onSend, value }: MessageComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 44), 152)}px`;
  }, [value]);

  const sendDisabled = disabled || !value.trim();

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文等输入法正在组词时，Enter 用于确认候选词，不能当成发送
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key !== "Enter") return;
    // Shift + Enter 换行，交回浏览器默认行为
    if (event.shiftKey) return;
    if (sendDisabled) return;
    event.preventDefault();
    onSend();
  };

  return (
    <form
      className="message-composer"
      onSubmit={(event) => {
        event.preventDefault();
        if (!sendDisabled) onSend();
      }}
    >
      <label className="sr-only" htmlFor="chat-composer">
        输入问题
      </label>
      <textarea
        disabled={disabled}
        id="chat-composer"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入问题，Enter 发送，Shift + Enter 换行"
        ref={textareaRef}
        rows={1}
        value={value}
      />
      <IconButton
        disabled={sendDisabled}
        icon={<Send aria-hidden="true" size={17} />}
        label="发送消息"
        size="small"
        type="submit"
      />
    </form>
  );
}
