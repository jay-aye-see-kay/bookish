export interface Health {
  status: string;
  catalog: number;
  corpus: number;
  embed_server: "ok" | "down";
}

export interface Book {
  work_key: string;
  author: string;
  title: string;
  year: number;
  editions: number;
  has_embedding: boolean;
}

export type RatingValue = -2 | -1 | 1 | 2;

export interface Rating {
  work_key: string;
  author: string;
  title: string;
  year: number;
  rating: RatingValue;
}

export interface Recommendation {
  work_key: string;
  author: string;
  title: string;
  year: number;
  editions: number;
  score: number;
}

/** Shared metadata subset used to render a rateable row. */
export interface BookMeta {
  work_key: string;
  author: string;
  title: string;
  year: number;
}
