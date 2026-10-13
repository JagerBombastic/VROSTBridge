// Test the actual patched host cache, not a second implementation.
#[path="../../upstream/ALVR/alvr/server_core/src/tracking/native_hands.rs"]
mod native_hands;

#[cfg(test)]
mod wire_tests {
    use alvr_common::Pose;
    use alvr_packets::{TrackingData, FaceData, NativeOpenvrHand, NativeOpenvrHandUpdate};
    use std::time::Duration;
    #[test]
    fn exact_source_curls_both_ranges_and_aux_survive_wire() {
        let make_hand=|offset:f32| {
            let mut ranges=[[Pose::IDENTITY;31];2];
            for (r,range) in ranges.iter_mut().enumerate() {
                for (bone,pose) in range.iter_mut().enumerate() {
                    pose.position.x=offset+r as f32/10.0+bone as f32/100.0;
                }
            }
            NativeOpenvrHandUpdate::Sample(Box::new(NativeOpenvrHand {
                sampled_at: Duration::from_millis(100),ranges,curls:Some([0.07,0.13,0.37,0.61,0.89]),
            }))
        };
        let mut packet=TrackingData {
            poll_timestamp: Duration::from_millis(104), device_motions:vec![], hand_skeletons:[None,None],
            native_openvr_hands:[make_hand(0.0),make_hand(0.5)],face:FaceData::default(),body:None,
        };
        let config=bincode::config::standard();
        let encoded=bincode::serde::encode_to_vec(&packet,config).unwrap();
        let (decoded,used): (TrackingData,usize)=bincode::serde::decode_from_slice(&encoded,config).unwrap();
        assert_eq!(used,encoded.len());
        for (hand,update) in decoded.native_openvr_hands.into_iter().enumerate() {
            let NativeOpenvrHandUpdate::Sample(sample)=update else { panic!("lost native hand") };
            assert!(sample.valid_at(decoded.poll_timestamp));
            assert_eq!(sample.curls,Some([0.07,0.13,0.37,0.61,0.89]));
            let offset=hand as f32*0.5;
            assert!((sample.ranges[0][30].position.x-(offset+0.3)).abs()<1e-6);
            assert!((sample.ranges[1][30].position.x-(offset+0.4)).abs()<1e-6);
        }
        packet.native_openvr_hands=std::array::from_fn(|_| NativeOpenvrHandUpdate::Unchanged(Duration::from_millis(100)));
        let small=bincode::serde::encode_to_vec(&packet,config).unwrap();
        assert!(small.len()<encoded.len()/10,"unchanged hand samples must not duplicate full bones");
        assert!(encoded.len()<4096,"full hands exceed expected bounded tracking payload");
    }
}
