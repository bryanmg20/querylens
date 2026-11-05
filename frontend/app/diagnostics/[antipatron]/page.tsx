import AntipatternDetail from "@/components/antipatterns/AntipatternDetail";

export default function AntipatternPage({ params }: { params: { antipatron: string } }) {
  return <AntipatternDetail id={params.antipatron} />;
}
