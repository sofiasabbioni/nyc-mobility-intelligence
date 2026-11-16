import summarize_memory
import sample_docker_stats as sampler


def test_to_mb_and_service_name():
    assert sampler.to_mb("512MiB") == 512
    assert round(sampler.to_mb("1.5GiB")) == 1536
    assert round(sampler.to_mb("1024KiB"), 3) == 1
    assert sampler.to_mb("garbage") == 0
    assert sampler.service_name("taxi_project-db-1") == "db"
    assert sampler.service_name("taxi_project-benchmark-1") == "benchmark"
    assert sampler.service_name("other") == "other"


def test_peak_memory_uses_the_right_container_and_window():
    samples = [{"ts": "10", "service": "db", "mem_mb": "300"}, {"ts": "12", "service": "db", "mem_mb": "700"},
               {"ts": "12", "service": "benchmark", "mem_mb": "999"}, {"ts": "20", "service": "db", "mem_mb": "5000"},
               {"ts": "30", "service": "benchmark", "mem_mb": "200"}]
    phases = [{"engine": "postgres", "months": "1", "start": "9", "end": "15"},
              {"engine": "duckdb", "months": "1", "start": "25", "end": "35"},
              {"engine": "duckdb", "months": "2", "start": "100", "end": "110"}]
    out = summarize_memory.peaks(samples, phases)
    assert out[0] == ["postgres", "1", "db", "700", 2]
    assert out[1] == ["duckdb", "1", "benchmark", "200", 1]
    assert out[2][3] == "" and out[2][4] == 0          # no samples: left empty, never invented
