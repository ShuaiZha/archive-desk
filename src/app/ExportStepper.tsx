import { Checkmark24Regular } from "@fluentui/react-icons";

const taskSteps = ["任务配置", "扫描预估", "下载", "完成"] as const;

type ExportStepperProps = {
  currentStep: 1 | 2 | 3 | 4;
  className?: string;
};

export function ExportStepper({ currentStep, className = "" }: ExportStepperProps) {
  return (
    <ol className={`export-stepper ${className}`.trim()} aria-label="任务进度">
      {taskSteps.map((label, index) => {
        const number = (index + 1) as 1 | 2 | 3 | 4;
        const complete = number < currentStep || currentStep === 4;
        const active = number === currentStep;
        return (
          <li key={label} className={`${complete ? "is-complete" : ""} ${active ? "is-active" : ""}`} aria-current={active ? "step" : undefined}>
            <span className="step-marker">{complete ? <Checkmark24Regular /> : number}</span>
            <span><strong>{number}. {label}</strong><small>{complete ? "已完成" : active ? "进行中" : "待开始"}</small></span>
          </li>
        );
      })}
    </ol>
  );
}
