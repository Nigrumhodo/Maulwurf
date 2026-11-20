import type { AudioListItemOut } from "./audios";
import type { Subject } from "./subjects";
import type { Task } from "./tasks";

// D4.4 — Per-subject statistics computed solely from persisted metadata and text status
// counters. Audio is never retained, downloaded, or played here (G1 invariant).

export interface SubjectBreakdownItem {
  id: string;
  name: string;
  color: string;
  totalClasses: number;
  readyClasses: number;
  transcribedMinutes: number;
  pendingTasks: number;
  confirmedTasks: number;
  completedTasks: number;
}

export interface GlobalSubjectStats {
  subjectCount: number;
  classCount: number;
  readyClassCount: number;
  transcribedMinutes: number;
  pendingTaskCount: number;
  confirmedTaskCount: number;
  completedTaskCount: number;
  subjects: SubjectBreakdownItem[];
}

export function computeSubjectStats(
  subjects: readonly Subject[],
  audios: readonly AudioListItemOut[],
  tasks: readonly Task[],
): GlobalSubjectStats {
  const map = new Map<string, SubjectBreakdownItem>();

  for (const subject of Array.isArray(subjects) ? subjects : []) {
    map.set(subject.id, {
      id: subject.id,
      name: subject.name,
      color: subject.color,
      totalClasses: 0,
      readyClasses: 0,
      transcribedMinutes: 0,
      pendingTasks: 0,
      confirmedTasks: 0,
      completedTasks: 0,
    });
  }

  let readyClassCount = 0;
  let totalSeconds = 0;
  const secondsBySubject = new Map<string, number>();

  for (const audio of Array.isArray(audios) ? audios : []) {
    const existing = map.get(audio.subject.id) ?? {
      id: audio.subject.id,
      name: audio.subject.name,
      color: audio.subject.color,
      totalClasses: 0,
      readyClasses: 0,
      transcribedMinutes: 0,
      pendingTasks: 0,
      confirmedTasks: 0,
      completedTasks: 0,
    };
    existing.totalClasses += 1;
    if (audio.aggregate_status === "ready") {
      existing.readyClasses += 1;
      readyClassCount += 1;
    }
    if (typeof audio.duration_seconds === "number" && audio.duration_seconds > 0) {
      totalSeconds += audio.duration_seconds;
      secondsBySubject.set(
        existing.id,
        (secondsBySubject.get(existing.id) ?? 0) + audio.duration_seconds,
      );
    }
    map.set(existing.id, existing);
  }

  for (const [subjectId, seconds] of secondsBySubject) {
    const entry = map.get(subjectId);
    if (entry !== undefined) {
      entry.transcribedMinutes = Math.round(seconds / 60);
    }
  }

  let pendingTaskCount = 0;
  let confirmedTaskCount = 0;
  let completedTaskCount = 0;

  for (const task of Array.isArray(tasks) ? tasks : []) {
    const isPending = task.status === "pending" || task.status === "needs_review";
    const isConfirmed = task.status === "confirmed";
    const isCompleted = task.status === "completed";
    if (isPending) pendingTaskCount += 1;
    if (isConfirmed) confirmedTaskCount += 1;
    if (isCompleted) completedTaskCount += 1;

    if (task.subject !== null) {
      const entry = map.get(task.subject.id);
      if (entry !== undefined) {
        if (isPending) entry.pendingTasks += 1;
        if (isConfirmed) entry.confirmedTasks += 1;
        if (isCompleted) entry.completedTasks += 1;
      }
    }
  }

  return {
    subjectCount: subjects.length,
    classCount: audios.length,
    readyClassCount,
    transcribedMinutes: Math.round(totalSeconds / 60),
    pendingTaskCount,
    confirmedTaskCount,
    completedTaskCount,
    subjects: [...map.values()],
  };
}
