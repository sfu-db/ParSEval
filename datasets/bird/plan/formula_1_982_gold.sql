{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "DATE"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "11",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 19,
          "name": "$19",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 22,
          "name": "$22",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 23,
          "name": "$23",
          "type": "DATE"
        },
        {
          "kind": "INPUT_REF",
          "index": 24,
          "name": "$24",
          "type": "VARCHAR"
        }
      ],
      "id": "10",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 2,
                "name": "$2",
                "type": "INTEGER"
              },
              {
                "kind": "SCALAR_QUERY",
                "operator": "$SCALAR_QUERY",
                "operands": [],
                "query": [
                  {
                    "relOp": "LogicalProject",
                    "project": [
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      }
                    ],
                    "id": "3",
                    "inputs": [
                      {
                        "relOp": "LogicalSort",
                        "sort": [
                          {
                            "column": 1,
                            "type": "DATE"
                          }
                        ],
                        "dir": [
                          "DESCENDING"
                        ],
                        "offset": 0,
                        "limit": 1,
                        "id": "2",
                        "inputs": [
                          {
                            "relOp": "LogicalProject",
                            "project": [
                              {
                                "kind": "INPUT_REF",
                                "index": 0,
                                "name": "$0",
                                "type": "INTEGER"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 6,
                                "name": "$6",
                                "type": "DATE"
                              }
                            ],
                            "id": "1",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "drivers",
                                "id": "0",
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
          "id": "9",
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
                    "index": 1,
                    "name": "$1",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 18,
                    "name": "$18",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "8",
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
                        "index": 2,
                        "name": "$2",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 9,
                        "name": "$9",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "6",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "qualifying",
                      "id": "4",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "drivers",
                      "id": "5",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "races",
                  "id": "7",
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