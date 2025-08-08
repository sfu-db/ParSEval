{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    },
    {
      "column": 1,
      "type": "VARCHAR"
    },
    {
      "column": 2,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "14",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 15,
          "name": "$15",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 16,
          "name": "$16",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 20,
          "name": "$20",
          "type": "VARCHAR"
        }
      ],
      "id": "13",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 3,
                "name": "$3",
                "type": "FLOAT"
              },
              {
                "kind": "SCALAR_QUERY",
                "operator": "$SCALAR_QUERY",
                "operands": [],
                "query": [
                  {
                    "relOp": "LogicalAggregate",
                    "keys": [],
                    "aggs": [
                      {
                        "operator": "AVG",
                        "distinct": false,
                        "ignoreNulls": false,
                        "operands": [
                          {
                            "column": 0,
                            "type": "FLOAT"
                          }
                        ],
                        "type": "FLOAT",
                        "name": "EXPR$0"
                      }
                    ],
                    "id": "6",
                    "inputs": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
                          {
                            "kind": "INPUT_REF",
                            "index": 3,
                            "name": "$3",
                            "type": "FLOAT"
                          }
                        ],
                        "id": "5",
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
                                  "index": 14,
                                  "name": "$14",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "INPUT_REF",
                                  "index": 5,
                                  "name": "$5",
                                  "type": "VARCHAR"
                                }
                              ]
                            },
                            "id": "4",
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
                                      "index": 6,
                                      "name": "$6",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 7,
                                      "name": "$7",
                                      "type": "VARCHAR"
                                    }
                                  ]
                                },
                                "id": "2",
                                "inputs": [
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "expense",
                                    "id": "0",
                                    "inputs": []
                                  },
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "budget",
                                    "id": "1",
                                    "inputs": []
                                  }
                                ]
                              },
                              {
                                "relOp": "LogicalTableScan",
                                "table": "member",
                                "id": "3",
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
          "id": "12",
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
                    "index": 14,
                    "name": "$14",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 5,
                    "name": "$5",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "11",
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
                        "index": 6,
                        "name": "$6",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 7,
                        "name": "$7",
                        "type": "VARCHAR"
                      }
                    ]
                  },
                  "id": "9",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "expense",
                      "id": "7",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "budget",
                      "id": "8",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "member",
                  "id": "10",
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