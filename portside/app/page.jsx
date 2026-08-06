import Navbar from "@/components/Navbar";
import Hero from "@/components/Hero";
import Services from "@/components/Services";
import Comparison from "@/components/Comparison";
import HowItWorks from "@/components/HowItWorks";
import Quickstart from "@/components/Quickstart";
import ContactForm from "@/components/ContactForm";
import Footer from "@/components/Footer";

export default function Home() {
  return (
    <main className="min-h-screen bg-yard-950">
      <Navbar />
      <Hero />
      <Services />
      <Comparison />
      <HowItWorks />
      <Quickstart />
      <ContactForm />
      <Footer />
    </main>
  );
}
