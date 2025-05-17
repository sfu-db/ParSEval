{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "8",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "VARCHAR"
        }
      ],
      "id": "7",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "NOT",
            "operator": "NOT",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "IN",
                "operator": "IN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 2,
                    "name": "$2",
                    "type": "VARCHAR"
                  }
                ],
                "query": [
                  {
                    "relOp": "LogicalAggregate",
                    "keys": [
                      {
                        "column": 0,
                        "type": "VARCHAR"
                      }
                    ],
                    "aggs": [],
                    "id": "4",
                    "inputs": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "VARCHAR"
                          }
                        ],
                        "id": "3",
                        "inputs": [
                          {
                            "relOp": "LogicalJoin",
                            "joinType": "inner",
                            "condition": {
                              "kind": "EQUALS",
                              "operator": "=",
                              "type": "BOOLEAN",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 0,
                                  "name": "$0",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "INPUT_REF",
                                  "index": 3,
                                  "name": "$3",
                                  "type": "VARCHAR"
                                }
                              ]
                            },
                            "id": "2",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "atom",
                                "id": "0",
                                "inputs": []
                              },
                              {
                                "relOp": "LogicalTableScan",
                                "table": "connected",
                                "id": "1",
                                "inputs": []
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "6",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "atom",
              "id": "5",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}