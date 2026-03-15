# Continuous Task Hook Prompt

You are running a **continuous-task strategy pass** after a task has reached a terminal state.

Your goal is to decide the best next stage and **auto-dispatch** it.

## Context

- Original task focus:
{task_prompt}

- User-specific continuous guidance:
{user_guidance}

## Mandatory workflow

1. Review what was delivered and what likely remains weak.
2. Research comparable products and credible technical references (industry + academic) relevant to this scope.
3. Model multiple user scenarios (new user, advanced user, and failure/edge-case user).
4. Identify the highest-impact next capabilities across UX, reliability, performance, safety, and maintainability.
5. Choose the **single best next follow-up task** for now.

## Required action

- Use `tasks_create` to auto-dispatch the next follow-up task immediately.
- The follow-up prompt must include:
  - concrete scope and acceptance criteria
  - implementation depth expectations
  - testing/validation requirements
  - a clear bullet-point execution plan
- Include an `on_complete` hook that continues this continuous-task loop with updated context.

## Terminal-state handling

- If the previous task **failed/cancelled**, prioritize stabilization/recovery before feature expansion.
- If the previous task **completed**, prioritize the highest-value new capability with measurable user impact.
