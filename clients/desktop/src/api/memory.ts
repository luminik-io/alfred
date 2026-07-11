import type { MemoryCandidateActionResponse } from "../types";
import { writeAlfredJson } from "./client";

export async function promoteMemoryCandidate(
  baseUrl: string,
  candidateId: string,
): Promise<MemoryCandidateActionResponse> {
  return writeAlfredJson(
    baseUrl,
    `/api/memory/candidates/${memoryPathSegment(candidateId)}/promote`,
    {},
  );
}

export async function rejectMemoryCandidate(
  baseUrl: string,
  candidateId: string,
): Promise<MemoryCandidateActionResponse> {
  return writeAlfredJson(
    baseUrl,
    `/api/memory/candidates/${memoryPathSegment(candidateId)}/reject`,
    {},
  );
}

// The AMS memory id a promoted lesson surfaces under. Auto-remembered lessons
// carry this deterministic id (see fleet_brain._lesson_memory_id); stripping it
// recovers the candidate id the retire route validates.
const LESSON_MEMORY_ID_PREFIX = "lesson:memory_candidate:";

export function lessonCandidateId(lessonId: string): string {
  const clean = (lessonId || "").trim();
  return clean.startsWith(LESSON_MEMORY_ID_PREFIX)
    ? clean.slice(LESSON_MEMORY_ID_PREFIX.length)
    : clean;
}

// True only for lessons backed by a memory_candidate row (the ones retire can
// walk back). /api/memory/lessons returns EVERY recalled lesson from the
// provider chain, including synced or directly-reflected lessons whose ids are
// not the candidate recall id; those have no candidate to retire, so the UI must
// not offer Undo on them (the retire route would 404).
export function isCandidateBackedLesson(lessonId: string): boolean {
  const clean = (lessonId || "").trim();
  return clean.startsWith(LESSON_MEMORY_ID_PREFIX) && clean.length > LESSON_MEMORY_ID_PREFIX.length;
}

// Undo an auto-remembered lesson: forget it from recall and retire the row.
// Accepts the lesson's recall id (or a bare candidate id) and sends the bare
// candidate id the server route validates.
export async function retireMemoryLesson(
  baseUrl: string,
  lessonId: string,
): Promise<MemoryCandidateActionResponse> {
  return writeAlfredJson(
    baseUrl,
    `/api/memory/candidates/${memoryPathSegment(lessonCandidateId(lessonId))}/retire`,
    {},
  );
}

function memoryPathSegment(candidateId: string): string {
  const clean = candidateId.trim();
  if (!/^[A-Za-z0-9:_-]+$/.test(clean)) {
    throw new Error("Memory candidate id is not safe to send to Alfred serve.");
  }
  return clean;
}
