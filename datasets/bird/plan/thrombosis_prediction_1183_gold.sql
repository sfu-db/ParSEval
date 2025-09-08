{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
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
                        "id": "2",
                        "inputs": [
                          {
                            "relOp": "LogicalFilter",
                            "condition": {
                              "kind": "AND",
                              "operator": "AND",
                              "type": "BOOLEAN",
                              "operands": [
                                {
                                  "kind": "EQUALS",
                                  "operator": "=",
                                  "type": "BOOLEAN",
                                  "operands": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 4,
                                      "name": "$4",
                                      "type": "DATE"
                                    },
                                    {
                                      "kind": "CAST",
                                      "operator": "CAST",
                                      "type": "DATE",
                                      "operands": [
                                        {
                                          "kind": "LITERAL",
                                          "value": "1991-06-13",
                                          "type": "CHAR",
                                          "nullable": false,
                                          "precision": 10
                                        }
                                      ]
                                    }
                                  ]
                                },
                                {
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
                                      "kind": "LITERAL",
                                      "value": "SJS",
                                      "type": "VARCHAR",
                                      "nullable": false,
                                      "precision": -1
                                    }
                                  ]
                                }
                              ]
                            },
                            "variableset": "[]",
                            "id": "1",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "Patient",
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
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "OTHER_FUNCTION",
                    "operator": "STRFTIME",
                    "type": "VARCHAR",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "%Y",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 2
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "TIMESTAMP",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 1,
                                "name": "$1",
                                "type": "DATE"
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": "1995",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Laboratory",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}