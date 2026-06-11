export enum ChangeType {
  UNCHANGED = 'UNCHANGED',
  ADDED = 'ADDED',
  REMOVED = 'REMOVED'
}

export interface Document {
  id: string;
  name: string;
  text: string;
}

export interface DiffSegment {
  id: string;
  text: string;
  type: ChangeType;
}
