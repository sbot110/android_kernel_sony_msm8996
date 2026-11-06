# Stage G.2 Kryo CPU Clock Hardware Safety & CPUHP Architecture

## 1. Background
The Qualcomm MSM8996 SoC features a custom quad-core Kryo CPU organized into two distinct clusters:
- **PWRCL (Power Cluster / Silver):** CPU 0 and CPU 1, max frequency ~1593 MHz
- **PERFCL (Performance Cluster / Gold):** CPU 2 and CPU 3, max frequency ~2150 MHz

Each cluster is managed by its own L2 cache controller with local Adaptive Clock Distribution (ACD) indirect system registers.

## 2. Hardware Ordering Constraint: L2ACDCR before SSSCTL
### The Silicon Race Condition
Qualcomm hardware documentation and Sony 4.4 kernel sources dictate that the Subsystem Select Control register (`SSSCTL`, offset `0x160`) must **only** be written after the cluster's local L2 ACD Control Register (`L2ACDCR`, indirect reg `0x580`) has been fully programmed.

Programming `SSSCTL` with `0xf` while `L2ACDCR` is uninitialized causes the Kryo hardware to engage ACD clock-gating against an uncalibrated divider, resulting in CPU lockup or bus stalls.

### Cluster Affinity & Indirect Register Locality
The L2 indirect registers (`sys_reg(3, 3, 15, 0, 6/7)`) are implemented inside the local cluster's hardware block. When CPU 0 (Cluster 0) boots and executes the clock driver probe, reading or writing L2 indirect registers interacts strictly with Cluster 0. It is physically impossible for CPU 0 to configure the L2 registers of Cluster 1.

Consequently, if CPU 0 unconditionally writes `writel(0xf, reg_base + PERFCL_REG_OFFSET + SSSCTL_OFFSET)` before Cluster 1 cores (CPU 2/CPU 3) have booted and initialized their local `L2ACDCR`, Cluster 1 enters an invalid hardware state.

## 3. Dynamic CPU Hotplug Integration (CPUHP)
To guarantee 100% hardware compliance across cold boot, secondary core bring-up, and CPU hotplugging, `drivers/clk/qcom/clk-cpu-8996.c` was redesigned:

1. **Affinity Detection:**
   ```c
   mpidr = read_cpuid_mpidr();
   cluster = mpidr & CPU_CLUSTER_AFFINITY_MASK;
   ```
2. **Cluster-Specific SSSCTL Programming:**
   ```c
   if (cluster == PERFCL_CLUSTER_ID)
       writel(0xf, reg_base + PERFCL_REG_OFFSET + SSSCTL_OFFSET);
   else
       writel(0xf, reg_base + PWRCL_REG_OFFSET + SSSCTL_OFFSET);
   ```
3. **Dynamic Online Callback:**
   A dynamic CPUHP state is registered via:
   ```c
   cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "clk/qcom/cpu8996:online",
                     qcom_cpu_clk_msm8996_cpu_online, NULL);
   ```
   When secondary cores (CPU 1, CPU 2, CPU 3) are brought online by PSCI, the online handler runs directly on that physical core. For CPU 2 and CPU 3, it writes Cluster 1's `L2ACDTD`, `L2ACDDVMRC`, `L2ACDSSCR`, `L2ACDCR`, and only then programs `PERFCL SSSCTL`.

4. **Resource Lifecycle:**
   On driver teardown, `cpuhp_remove_state()` ensures no dangling hotplug callbacks remain.
