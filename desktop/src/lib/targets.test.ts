import { describe, expect, it } from "vitest";
import { notificationTarget } from "./targets";

describe("notification targets", () => {
  it("opens the coding job, preferred over its task", () => {
    expect(notificationTarget("coding.finished", { job_id: "j1", task_id: "t1", status: "failed" })).toEqual({ view: "agents", job: "j1" });
    expect(notificationTarget("coding.interrupted", { job_id: "j2" })).toEqual({ view: "agents", job: "j2" });
  });

  it("opens the task or the approvals", () => {
    expect(notificationTarget("task.failed", { task_id: "t1" })).toEqual({ view: "tasks", task: "t1" });
    expect(notificationTarget("approval.requested", { approval_id: "a1", task_id: "t1" })).toEqual({ view: "approvals" });
  });

  it("falls back to the list of the event family, or nothing", () => {
    expect(notificationTarget("task.completed", {})).toEqual({ view: "tasks" });
    expect(notificationTarget("coding.finished", null)).toEqual({ view: "agents" });
    expect(notificationTarget("agent.usage", { level: "warning" })).toBeNull();
    expect(notificationTarget("task.failed", { task_id: 42 })).toEqual({ view: "tasks" });
  });

  it("opens Social Media for a new or failed video", () => {
    expect(notificationTarget("social.ready", { video_id: "v1" })).toEqual({ view: "social" });
    expect(notificationTarget("social.failed", { video_id: "v1" })).toEqual({ view: "social" });
  });
});
