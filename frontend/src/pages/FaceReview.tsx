// Quick yes/no review of uncertain matches ("Is this Sue?").
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, faceThumbUrl, post, type ReviewFace } from "../api";
import { useViewer } from "../components/MediaViewer";
import { Loading, useToast } from "../ui";

export default function FaceReview() {
  const qc = useQueryClient();
  const toast = useToast();
  const viewer = useViewer();
  const q = useQuery({ queryKey: ["people-review"], queryFn: () => api<ReviewFace[]>("/api/people/review") });
  const act = useMutation({
    mutationFn: ({ path, ids }: { path: string; ids: number[] }) => post(path, { face_ids: ids }),
    onMutate: ({ ids }) => {
      qc.setQueryData<ReviewFace[]>(["people-review"], (old) => (old ?? []).filter((f) => !ids.includes(f.face_id)));
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["person"] });
    },
    onError: (e) => {
      toast(String(e instanceof Error ? e.message : e), true);
      qc.invalidateQueries({ queryKey: ["people-review"] });
    },
  });

  if (q.isLoading) return <Loading />;
  const rows = q.data ?? [];
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="small muted"><Link to="/people">People</Link> ›</div>
          <h1>Is this…?</h1>
        </div>
        {rows.length > 0 && (
          <button className="btn primary" onClick={() => act.mutate({ path: "/api/faces/confirm", ids: rows.map((r) => r.face_id) })}>
            Yes to all {rows.length}
          </button>
        )}
      </div>
      {rows.length === 0 ? (
        <div className="card empty"><div className="big">✅</div>Nothing to review right now.</div>
      ) : (
        <div className="review-faces">
          {rows.map((r) => (
            <div key={r.face_id} className="card review-face">
              <button className="review-face-img" onClick={() => viewer.open([{ item: r.item_id, file: r.file_id }])} title="See the photo">
                <img src={faceThumbUrl(r.face_id)} alt="" loading="lazy" />
              </button>
              <div className="small" style={{ textAlign: "center" }}>
                <strong>{r.suggested_name}</strong>?
                <div className="faint">{Math.round(r.similarity * 100)}% match</div>
              </div>
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <button className="btn keep grow" onClick={() => act.mutate({ path: "/api/faces/confirm", ids: [r.face_id] })}>Yes</button>
                <button className="btn grow" onClick={() => act.mutate({ path: "/api/faces/unassign", ids: [r.face_id] })}>No</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
