import { FolderGit2 } from "lucide-react";
import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { EmptyState, ErrorBox, Spinner } from "./components/ui";
import { useCurrentProject } from "./hooks/queries";
import ChatPage from "./pages/ChatPage";
import EvaluationPage from "./pages/EvaluationPage";
import FeedbackPage from "./pages/FeedbackPage";
import ManagerPage from "./pages/ManagerPage";
import OnboardingPage from "./pages/OnboardingPage";
import OverviewPage from "./pages/OverviewPage";
import ProjectsPage from "./pages/ProjectsPage";
import type { Project } from "./api/types";

/** Pages that need a selected project render through this guard. */
function WithProject({ children }: { children: (project: Project) => ReactNode }) {
  const { project, projects } = useCurrentProject();
  if (projects.isLoading) return <Spinner />;
  if (projects.error) return <ErrorBox error={projects.error} onRetry={() => projects.refetch()} />;
  if (!project) {
    return (
      <EmptyState
        icon={<FolderGit2 className="size-8" />}
        title="No project selected"
        action={
          <Link className="text-sm font-medium text-accent-ink underline" to="/projects">
            Add a project
          </Link>
        }
      >
        Add a local folder or a GitHub repository on the Projects page to get started.
      </EmptyState>
    );
  }
  return <>{children(project)}</>;
}

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/overview" element={<WithProject>{(p) => <OverviewPage project={p} />}</WithProject>} />
        <Route path="/chat" element={<WithProject>{(p) => <ChatPage project={p} />}</WithProject>} />
        <Route path="/onboarding" element={<WithProject>{(p) => <OnboardingPage project={p} />}</WithProject>} />
        <Route path="/manager" element={<ManagerPage />} />
        <Route path="/feedback" element={<WithProject>{(p) => <FeedbackPage project={p} />}</WithProject>} />
        <Route path="/evaluation" element={<EvaluationPage />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </Layout>
  );
}
