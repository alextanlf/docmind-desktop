import { Send } from "lucide-react";
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
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !sendDisabled) {
            event.preventDefault();
            onSend();
          }
        }}
        placeholder="输入问题，按 Cmd + Enter 发送"
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
